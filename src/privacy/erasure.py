"""
Apagamento de titular — M4, Fase 7 / Fase B1 (LGPD Art. 18).

erase_subject() implementa o passo 3c do M1 (docs/m1-consent-lifecycle.md):
  a. Evento consent.erasure_requested
  b. Revogar todos os consentimentos não-REVOKED do titular
  c. Apagar todos os snapshots do titular
  c2. Apagar todas as decisões do titular (decision_traces)
  d. Crypto-shredding do cofre de identidade
  e. Evento erasure.completed

Idempotente: segunda chamada retorna contagens zero.
Nunca loga CPF.

Pedido por CPF (erase_subject_by_cpf):
  O titular informa o CPF, não o pseudonym_id. Depois de uma rotação da chave
  de pseudonimização o mesmo CPF tem um pseudonym_id por versão; o apagamento
  roda erase_subject() para cada um. O CPF só é usado em memória para calcular
  os pseudônimos; nunca é logado, auditado ou devolvido.

Falha de auditoria:
  - Se o evento (a) falhar (AuditWriteError) → propagar; nada é apagado.
  - Se o evento (e) falhar → apagamento já realizado; ErasureReport.completion_audited=False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.core.clock import to_iso as _to_iso


@dataclass
class ErasureReport:
    consents_revoked: int
    snapshots_deleted: int
    decisions_deleted: int
    vault_shredded: bool
    trace_id: str
    completion_audited: bool = True


def erase_subject(
    pseudonym_id: str,
    *,
    store,
    vault,
    audit,
    now: Optional[datetime] = None,
) -> ErasureReport:
    """
    Apaga todos os dados do titular identificado por pseudonym_id.

    Parâmetros:
        pseudonym_id — ID pseudonimizado do titular (HMAC-SHA256).
        store        — RetentionStore: iter_consents, update_consent, iter_snapshots,
                       delete_snapshot.
        vault        — IdentityVault: shred(pseudonym_id) → bool.
        audit        — AuditLogger: record_event(...) → trace_id.
        now          — instante de referência; padrão: utcnow().

    Retorna ErasureReport com as contagens e o trace_id do evento inicial.

    Levanta AuditWriteError se o evento inicial (a) não puder ser gravado;
    nesse caso nenhum dado é apagado.
    """
    from src.privacy.audit import AuditWriteError  # local — evita import circular nos testes

    now_iso = _to_iso(now)

    # (a) Evento de início — se falhar, propaga; NADA é apagado.
    trace_id: str = audit.record_event(
        "consent.erasure_requested",
        pseudonym_id=pseudonym_id,
        actor="internal_api",
    )

    # (b) Revogar consentimentos não-REVOKED
    consents_revoked = 0
    for doc in store.iter_consents({"pseudonym_id": pseudonym_id}):
        if doc.get("status") == "REVOKED":
            continue
        doc["status"] = "REVOKED"
        doc["revocation_reason"] = "LGPD_ERASURE"
        doc["revoked_at"] = now_iso
        store.update_consent(doc)
        consents_revoked += 1

    # (c) Apagar snapshots
    snapshots_deleted = 0
    for doc in store.iter_snapshots({"pseudonym_id": pseudonym_id}):
        store.delete_snapshot(doc)
        snapshots_deleted += 1

    # (c2) Apagar decisões do titular (decision_traces)
    decisions_deleted = 0
    for doc in store.iter_decisions({"pseudonym_id": pseudonym_id}):
        store.delete_decision(doc)
        decisions_deleted += 1

    # (d) Crypto-shredding do cofre
    vault_shredded: bool = vault.shred(pseudonym_id)

    # (e) Evento de conclusão — falha aqui não desfaz o apagamento já feito.
    completion_audited = True
    try:
        audit.record_event(
            "erasure.completed",
            pseudonym_id=pseudonym_id,
            actor="internal_api",
            consents_revoked=consents_revoked,
            snapshots_deleted=snapshots_deleted,
            decisions_deleted=decisions_deleted,
            vault_shredded=vault_shredded,
            request_trace_id=trace_id,
        )
    except AuditWriteError:
        completion_audited = False

    return ErasureReport(
        consents_revoked=consents_revoked,
        snapshots_deleted=snapshots_deleted,
        decisions_deleted=decisions_deleted,
        vault_shredded=vault_shredded,
        trace_id=trace_id,
        completion_audited=completion_audited,
    )


@dataclass
class SubjectErasure:
    key_version: str
    pseudonym_id: str
    report: ErasureReport


@dataclass
class CpfErasureReport:
    subjects: list[SubjectErasure] = field(default_factory=list)

    @property
    def totals(self) -> dict:
        return {
            "consents_revoked": sum(s.report.consents_revoked for s in self.subjects),
            "snapshots_deleted": sum(s.report.snapshots_deleted for s in self.subjects),
            "decisions_deleted": sum(s.report.decisions_deleted for s in self.subjects),
            "vault_shredded": sum(1 for s in self.subjects if s.report.vault_shredded),
            "completion_audited": all(s.report.completion_audited for s in self.subjects),
        }

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return {
            "subjects": [
                {"key_version": s.key_version, "pseudonym_id": s.pseudonym_id, **asdict(s.report)}
                for s in self.subjects
            ],
            "totals": self.totals,
        }


def erase_subject_by_cpf(
    cpf: str,
    *,
    store,
    vault,
    audit,
    now: Optional[datetime] = None,
) -> CpfErasureReport:
    """
    Apaga os dados do titular em TODAS as versões da chave de pseudonimização.

    Levanta ValueError se o CPF não tiver 11 dígitos, PseudonymizationKeyError
    se alguma versão de chave não puder ser lida (antes de apagar qualquer
    coisa) e AuditWriteError se a auditoria inicial de algum pseudônimo falhar.
    Nesse último caso os pseudônimos anteriores já foram apagados; como
    erase_subject é idempotente, repetir o pedido completa o apagamento.
    """
    from src.privacy.pseudonymization import pseudonymize_cpf_all_versions

    # Calcula todos os pseudônimos ANTES de apagar: falha de chave aborta tudo.
    pseudonyms = pseudonymize_cpf_all_versions(cpf)

    result = CpfErasureReport()
    for pseudonym_id, key_version in pseudonyms:
        report = erase_subject(pseudonym_id, store=store, vault=vault, audit=audit, now=now)
        result.subjects.append(
            SubjectErasure(key_version=key_version, pseudonym_id=pseudonym_id, report=report)
        )
    return result
