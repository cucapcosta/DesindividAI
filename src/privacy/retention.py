"""
Job de retenção — M4, Fase 6 / Fase B1.

Substitui o TTL do MongoDB: percorre consent_registry, financial_snapshots,
identity_vault e decision_traces e aplica as regras de retenção do M1/M4.

Regras (nesta ordem):
  a.  Snapshots: expires_at (ou min(expires_at, revoked_at + RETENTION_AFTER_REVOCATION_DAYS)
      se o consentimento do snapshot estiver REVOKED) < now → apagar.
  a2. Decisões (simulation/trace): mesmo cálculo de limite que (a) mas usando o consent_id
      do documento; limite < now → apagar.  Documento sem expires_at → skipped_invalid.
  b.  Consentimento AWAITING_AUTHORISATION com awaiting_expires_at < now → apagar.
  c.  Consentimento AUTHORISED com expires_at < now → REVOKED, reason EXPIRED, audit consent.expired.
  d.  Consentimento REVOKED com revoked_at + RETENTION_AFTER_REVOCATION_DAYS < now → apagar.
  e.  Cofre: doc ativo (shredded_at null) com created_at há mais de 24h, sem consentimento nem
      snapshot com o mesmo pseudonym_id → IdentityVault.shred.
  f.  audit_trail: nunca tocar.

run_retention() é idempotente: segunda execução com o mesmo now → tudo zero.

CLI:
    python -m src.privacy.retention [--dry-run] [--ensure]
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from src.agents.agent1.dates import parse_utc
from src.privacy.audit import AuditLogger, get_audit_logger
from src.privacy.identity_vault import IdentityVault, get_identity_vault
from src.privacy.retention_store import RetentionStore, get_retention_store

logger = logging.getLogger(__name__)

_DEFAULT_RETENTION_AFTER_REVOCATION_DAYS = 90


def _get_retention_days() -> int:
    try:
        return int(os.getenv("RETENTION_AFTER_REVOCATION_DAYS", str(_DEFAULT_RETENTION_AFTER_REVOCATION_DAYS)))
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION_AFTER_REVOCATION_DAYS


# ---------------------------------------------------------------------------
# RetentionReport
# ---------------------------------------------------------------------------

@dataclass
class RetentionReport:
    snapshots_deleted: int = 0
    decisions_deleted: int = 0
    consents_awaiting_deleted: int = 0
    consents_expired: int = 0
    consents_revoked_deleted: int = 0
    vault_shredded: int = 0
    skipped_invalid: int = 0
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_safe(value: Optional[str], doc_id: str, field: str) -> Optional[datetime]:
    """
    Parse ISO-8601 date safely. Returns None (and logs warning) if invalid/absent.
    """
    if not value:
        return None
    try:
        return parse_utc(value)
    except (ValueError, TypeError):
        logger.warning(
            "retention: data inválida no campo '%s' do documento _id=%s — ignorando",
            field, doc_id,
        )
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# run_retention
# ---------------------------------------------------------------------------

def run_retention(
    now: Optional[datetime] = None,
    dry_run: bool = False,
    *,
    store: Optional[RetentionStore] = None,
    vault: Optional[IdentityVault] = None,
    audit: Optional[Callable[..., str]] = None,
) -> RetentionReport:
    """
    Executa o job de retenção e retorna o relatório com as contagens.

    Parâmetros injetáveis (para testes):
        now   — instante de referência (padrão: utcnow()).
        store — RetentionStore (padrão: get_retention_store()).
        vault — IdentityVault  (padrão: get_identity_vault()).
        audit — callable compatível com AuditLogger.record_event
                (padrão: get_audit_logger().record_event).
    dry_run=True: calcula tudo e NÃO escreve nada nos dados nem emite
                  consent.expired. Ao final emite audit retention.run com dry_run=True.
    """
    if now is None:
        now = _utcnow()

    if store is None:
        store = get_retention_store()

    if vault is None:
        vault = get_identity_vault()

    if audit is None:
        audit = get_audit_logger().record_event

    retention_days = _get_retention_days()
    retention_delta = timedelta(days=retention_days)

    report = RetentionReport(dry_run=dry_run)

    # -----------------------------------------------------------------------
    # Pre-build index: consentimentos REVOKED por pseudonym_id
    # (usado para calcular limite dos snapshots quando consent_doc não existe)
    # -----------------------------------------------------------------------
    revoked_consents: dict[str, datetime] = {}  # pseudonym_id → revoked_at
    for doc in store.iter_consents({"status": "REVOKED"}):
        pid = doc.get("pseudonym_id", "")
        raw_revoked = doc.get("revoked_at")
        if raw_revoked:
            dt = _parse_safe(raw_revoked, doc.get("_id", ""), "revoked_at")
            if dt is not None:
                # Keep the earliest revoked_at per pseudonym_id
                if pid not in revoked_consents or dt < revoked_consents[pid]:
                    revoked_consents[pid] = dt

    # Also build index of consents by consent_id for snapshot lookup
    consent_by_id: dict[str, dict] = {}  # consent_id → doc
    for doc in store.iter_consents({}):
        cid = doc.get("consent_id") or doc.get("_id", "")
        consent_by_id[cid] = doc

    # -----------------------------------------------------------------------
    # Regra a — Snapshots
    # Acumula pseudonym_ids de snapshots apagados para a regra e
    # -----------------------------------------------------------------------
    deleted_snapshot_pseudonym_ids: set[str] = set()

    for doc in store.iter_snapshots({}):
        doc_id = doc.get("_id", "")
        raw_expires = doc.get("expires_at")

        if not raw_expires:
            report.skipped_invalid += 1
            logger.warning(
                "retention: snapshot sem expires_at _id=%s — ignorando", doc_id
            )
            continue

        expires_at = _parse_safe(raw_expires, doc_id, "expires_at")
        if expires_at is None:
            report.skipped_invalid += 1
            continue

        # Verificar se consentimento está REVOKED → usar min(expires_at, revoked_at + delta)
        consent_id = doc.get("consent_id", "")
        consent_doc = consent_by_id.get(consent_id)
        limit = expires_at

        if consent_doc is not None and consent_doc.get("status") == "REVOKED":
            raw_revoked = consent_doc.get("revoked_at")
            if raw_revoked:
                revoked_at = _parse_safe(raw_revoked, consent_doc.get("_id", ""), "revoked_at")
                if revoked_at is not None:
                    candidate = revoked_at + retention_delta
                    if candidate < limit:
                        limit = candidate
        elif consent_doc is None:
            # Fallback por pseudonym_id: só quando o consentimento não existe mais
            pid = doc.get("pseudonym_id", "")
            if pid in revoked_consents:
                candidate = revoked_consents[pid] + retention_delta
                if candidate < limit:
                    limit = candidate
        # Se consent_doc existe e não está REVOKED, usa limit = expires_at (já definido)

        if limit < now:
            pid = doc.get("pseudonym_id", "")
            deleted_snapshot_pseudonym_ids.add(pid)
            if not dry_run:
                store.delete_snapshot(doc)
            report.snapshots_deleted += 1

    # -----------------------------------------------------------------------
    # Regra a2 — Decisões (simulation / trace)
    # -----------------------------------------------------------------------
    for doc in store.iter_decisions({"type": {"$in": ["simulation", "trace"]}}):
        doc_id = doc.get("_id", "")
        raw_expires = doc.get("expires_at")

        if not raw_expires:
            report.skipped_invalid += 1
            logger.warning(
                "retention: decisão sem expires_at _id=%s — ignorando", doc_id
            )
            continue

        expires_at = _parse_safe(raw_expires, doc_id, "expires_at")
        if expires_at is None:
            report.skipped_invalid += 1
            continue

        # Verificar se consentimento associado está REVOKED → min(expires_at, revoked_at + delta)
        consent_id = doc.get("consent_id", "")
        consent_doc = consent_by_id.get(consent_id)
        limit = expires_at

        if consent_doc is not None and consent_doc.get("status") == "REVOKED":
            raw_revoked = consent_doc.get("revoked_at")
            if raw_revoked:
                revoked_at = _parse_safe(raw_revoked, consent_doc.get("_id", ""), "revoked_at")
                if revoked_at is not None:
                    candidate = revoked_at + retention_delta
                    if candidate < limit:
                        limit = candidate

        if limit < now:
            if not dry_run:
                store.delete_decision(doc)
            report.decisions_deleted += 1

    # -----------------------------------------------------------------------
    # Regra b — AWAITING_AUTHORISATION expirados
    # Acumula pseudonym_ids de consentimentos apagados para a regra e
    # -----------------------------------------------------------------------
    deleted_consent_pseudonym_ids: set[str] = set()

    for doc in store.iter_consents({"status": "AWAITING_AUTHORISATION"}):
        doc_id = doc.get("_id", "")
        raw_awaiting = doc.get("awaiting_expires_at")

        if not raw_awaiting:
            report.skipped_invalid += 1
            logger.warning(
                "retention: consentimento AWAITING sem awaiting_expires_at _id=%s — ignorando",
                doc_id,
            )
            continue

        awaiting_expires = _parse_safe(raw_awaiting, doc_id, "awaiting_expires_at")
        if awaiting_expires is None:
            report.skipped_invalid += 1
            continue

        if awaiting_expires < now:
            pid = doc.get("pseudonym_id", "")
            deleted_consent_pseudonym_ids.add(pid)
            if not dry_run:
                store.delete_consent(doc)
            report.consents_awaiting_deleted += 1

    # -----------------------------------------------------------------------
    # Regra c — AUTHORISED expirados → REVOKED / consent.expired
    # -----------------------------------------------------------------------
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for doc in store.iter_consents({"status": "AUTHORISED"}):
        doc_id = doc.get("_id", "")
        raw_expires = doc.get("expires_at")

        if not raw_expires:
            report.skipped_invalid += 1
            logger.warning(
                "retention: consentimento AUTHORISED sem expires_at _id=%s — ignorando",
                doc_id,
            )
            continue

        expires_at = _parse_safe(raw_expires, doc_id, "expires_at")
        if expires_at is None:
            report.skipped_invalid += 1
            continue

        if expires_at < now:
            if not dry_run:
                doc["status"] = "REVOKED"
                doc["revocation_reason"] = "EXPIRED"
                doc["revoked_at"] = now_iso
                store.update_consent(doc)
                # emit audit event
                try:
                    audit(
                        "consent.expired",
                        pseudonym_id=doc.get("pseudonym_id"),
                        actor="retention",
                        consent_id=doc.get("consent_id") or doc_id,
                    )
                except Exception as exc:
                    logger.error(
                        "retention: falha ao emitir consent.expired para _id=%s: %s",
                        doc_id, type(exc).__name__,
                    )
            report.consents_expired += 1

    # -----------------------------------------------------------------------
    # Regra d — REVOKED há mais de RETENTION_AFTER_REVOCATION_DAYS → apagar
    # -----------------------------------------------------------------------
    for doc in store.iter_consents({"status": "REVOKED"}):
        doc_id = doc.get("_id", "")
        raw_revoked = doc.get("revoked_at")

        if not raw_revoked:
            report.skipped_invalid += 1
            logger.warning(
                "retention: consentimento REVOKED sem revoked_at _id=%s — ignorando",
                doc_id,
            )
            continue

        revoked_at = _parse_safe(raw_revoked, doc_id, "revoked_at")
        if revoked_at is None:
            report.skipped_invalid += 1
            continue

        if revoked_at + retention_delta < now:
            pid = doc.get("pseudonym_id", "")
            deleted_consent_pseudonym_ids.add(pid)
            if not dry_run:
                store.delete_consent(doc)
            report.consents_revoked_deleted += 1

    # -----------------------------------------------------------------------
    # Regra e — Cofre: sem consentimento nem snapshot → shred
    # (somente docs ativos com created_at há mais de 24h)
    # -----------------------------------------------------------------------
    carencia = timedelta(hours=24)

    # Build active consent pseudonym_ids AFTER rules b/c/d.
    # Re-iterate to reflect actual deletes (dry_run or not); then exclude
    # pseudonym_ids that were logically deleted in this run (fixes dry_run
    # under-counting: in dry_run nothing was removed from the store, but we
    # still need to consider those consents/snapshots as gone).
    active_pseudonym_ids: set[str] = set()
    for doc in store.iter_consents({}):
        pid = doc.get("pseudonym_id", "")
        if pid:
            active_pseudonym_ids.add(pid)
    # Exclude consents deleted (logically) in rules b/d
    active_pseudonym_ids -= deleted_consent_pseudonym_ids

    # Build active snapshot pseudonym_ids AFTER rule a.
    # Exclude snapshots deleted (logically) in rule a.
    active_snapshot_pseudonym_ids: set[str] = set()
    for doc in store.iter_snapshots({}):
        pid = doc.get("pseudonym_id", "")
        if pid:
            active_snapshot_pseudonym_ids.add(pid)
    active_snapshot_pseudonym_ids -= deleted_snapshot_pseudonym_ids

    for doc in store.iter_vault({"shredded_at": None}):
        doc_id = doc.get("_id", "")
        raw_created = doc.get("created_at")

        if not raw_created:
            report.skipped_invalid += 1
            logger.warning(
                "retention: vault sem created_at _id=%s — ignorando", doc_id
            )
            continue

        created_at = _parse_safe(raw_created, doc_id, "created_at")
        if created_at is None:
            report.skipped_invalid += 1
            continue

        # Only apply carência de 24h
        if created_at + carencia > now:
            continue

        pseudonym_id = doc_id  # _id = pseudonym_id in identity_vault

        has_consent = pseudonym_id in active_pseudonym_ids
        has_snapshot = pseudonym_id in active_snapshot_pseudonym_ids

        if not has_consent and not has_snapshot:
            if not dry_run:
                vault.shred(pseudonym_id)
            report.vault_shredded += 1

    # -----------------------------------------------------------------------
    # Evento retention.run (sempre, inclusive dry_run)
    # -----------------------------------------------------------------------
    try:
        audit(
            "retention.run",
            pseudonym_id=None,
            actor="retention",
            snapshots_deleted=report.snapshots_deleted,
            decisions_deleted=report.decisions_deleted,
            consents_awaiting_deleted=report.consents_awaiting_deleted,
            consents_expired=report.consents_expired,
            consents_revoked_deleted=report.consents_revoked_deleted,
            vault_shredded=report.vault_shredded,
            skipped_invalid=report.skipped_invalid,
            dry_run=dry_run,
        )
    except Exception as exc:
        logger.error(
            "retention: falha ao emitir retention.run: %s", type(exc).__name__
        )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Job de retenção M4")
    parser.add_argument("--dry-run", action="store_true", help="Não apaga nada; mostra o que faria")
    parser.add_argument("--ensure", action="store_true", help="Cria bancos e índices antes de rodar")
    args = parser.parse_args()

    if args.ensure:
        from src.agents.agent1.config import ConfigurationError

        url = os.getenv("CLOUDANT_URL", "")
        apikey = os.getenv("CLOUDANT_APIKEY", "")
        if not url or not apikey:
            raise ConfigurationError("CLOUDANT_URL e CLOUDANT_APIKEY são necessários para --ensure")

        from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
        from ibmcloudant.cloudant_v1 import CloudantV1

        from src.privacy.retention_store import ensure_databases, ensure_indexes

        client = CloudantV1(authenticator=IAMAuthenticator(apikey))
        client.set_service_url(url)
        ensure_databases(client)
        ensure_indexes(client)

    report = run_retention(dry_run=args.dry_run)
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    _main()
