"""
Acesso e portabilidade dos dados do titular — LGPD Art. 18, II, V e VII.

export_subject() reúne, para um ou mais pseudonym_id do mesmo titular (um por
versão da chave de pseudonimização), tudo o que o sistema guarda dele:

  - consents   — consent_registry
  - snapshots  — financial_snapshots (dívidas, CET, faixa de score)
  - decisions  — decision_traces (simulações e recomendações, com o texto que
                 o modelo gerou: serve de explicação da decisão automatizada,
                 Art. 20)
  - vault      — só metadados do cofre (existe, criado em, versão, destruído
                 em). O CPF cifrado e a DEK nunca saem.
  - audit_events — histórico de tratamento (event_type, recorded_at, actor e
                 campos do evento, sem trace interno de outros titulares)
  - sharing    — resumo dos compartilhamentos (Art. 18 VII): instituições
                 Open Finance autorizadas e consultas ao Serasa.

Campos internos do Cloudant (_rev) são removidos. A exportação é auditada com
subject.access_exported (só contagens). Nunca loga nem audita o CPF.
"""

from __future__ import annotations

from typing import Iterable

_INTERNAL_FIELDS = frozenset({"_rev"})
_VAULT_PUBLIC_FIELDS = ("created_at", "key_version", "shredded_at", "previously_shredded_at")
_AUDIT_EXPORT_LIMIT = 1000


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _INTERNAL_FIELDS}


def _vault_metadata(store, pseudonym_id: str) -> dict:
    for doc in store.iter_vault({"_id": pseudonym_id}):
        meta = {"exists": True}
        meta.update({k: doc.get(k) for k in _VAULT_PUBLIC_FIELDS if k in doc})
        return meta
    return {"exists": False}


def _sharing_summary(consents: Iterable[dict], audit_events: Iterable[dict]) -> dict:
    institutions: set[str] = set()
    for doc in consents:
        for inst in doc.get("institutions") or []:
            institutions.add(str(inst))
    serasa_queries = sum(
        1 for ev in audit_events
        if ev.get("event_type") == "vault.reveal"
        and ev.get("purpose") == "SERASA_QUERY"
        and ev.get("ok") is True
    )
    return {
        "open_finance_institutions": sorted(institutions),
        "serasa_queries": serasa_queries,
        "processors": [
            "IBM Cloud (Cloudant, Cloud Object Storage) — armazenamento",
            "IBM watsonx.ai — inferência sem dados de identificação",
        ],
    }


def export_subject(
    pseudonyms: list[tuple[str, str]],
    *,
    store,
    audit,
    actor: str = "internal_api",
) -> dict:
    """
    Exporta os dados do titular.

    pseudonyms — [(pseudonym_id, key_version), ...]
    store      — RetentionStore (iter_consents, iter_snapshots, iter_decisions, iter_vault)
    audit      — AuditLogger (find_events, record_event)

    Levanta AuditWriteError se o evento subject.access_exported não puder ser
    gravado; nesse caso nada é devolvido (acesso a dado pessoal sem trilha não
    é permitido).
    """
    subjects = []
    all_consents: list[dict] = []
    all_events: list[dict] = []

    for pseudonym_id, key_version in pseudonyms:
        consents = [_clean(d) for d in store.iter_consents({"pseudonym_id": pseudonym_id})]
        snapshots = [_clean(d) for d in store.iter_snapshots({"pseudonym_id": pseudonym_id})]
        decisions = [_clean(d) for d in store.iter_decisions({"pseudonym_id": pseudonym_id})]
        events = [
            _clean(d) for d in audit.find_events({"pseudonym_id": pseudonym_id}, limit=_AUDIT_EXPORT_LIMIT)
        ]
        events.sort(key=lambda e: e.get("recorded_at") or "")
        all_consents.extend(consents)
        all_events.extend(events)
        subjects.append({
            "pseudonym_id": pseudonym_id,
            "key_version": key_version,
            "vault": _vault_metadata(store, pseudonym_id),
            "consents": consents,
            "snapshots": snapshots,
            "decisions": decisions,
            "audit_events": events,
            "audit_events_truncated": len(events) >= _AUDIT_EXPORT_LIMIT,
        })

    export = {
        "subjects": subjects,
        "sharing": _sharing_summary(all_consents, all_events),
    }

    # Auditoria antes de devolver: sem trilha, sem acesso. Um evento por
    # pseudônimo, para que a busca da trilha por qualquer um deles o encontre.
    export["trace_ids"] = [
        audit.record_event(
            "subject.access_exported",
            pseudonym_id=s["pseudonym_id"],
            actor=actor,
            key_version=s["key_version"],
            consents=len(s["consents"]),
            snapshots=len(s["snapshots"]),
            decisions=len(s["decisions"]),
            audit_events=len(s["audit_events"]),
        )
        for s in subjects
    ]
    return export
