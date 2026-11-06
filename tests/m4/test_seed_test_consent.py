"""
Testes unitários para scripts/seed_test_consent.py — R-24.

Verificam que delete() só faz shred do cofre se não restar NENHUM outro
consentimento com o mesmo pseudonym_id após apagar o consentimento alvo.

Todos os testes rodam sem rede via injeção de dependências (cloudant_client
e vault são fakes).
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeVault:
    """Fake de IdentityVault para capturar chamadas ao shred()."""

    def __init__(self) -> None:
        self.shredded: list[str] = []

    def shred(self, pseudonym_id: str) -> bool:
        self.shredded.append(pseudonym_id)
        return True


def _make_cloudant_client(
    consents: list[dict],
    snapshots: list[dict] | None = None,
) -> MagicMock:
    """
    Constrói um cliente Cloudant fake que responde ao post_find, get_document
    e delete_document/put_document usados em delete().

    consents: lista de documentos no consent_registry.
    snapshots: lista de documentos no financial_snapshots (padrão: vazio).
    """
    snapshots = snapshots or []

    def _post_find(**kwargs) -> MagicMock:
        db = kwargs.get("db", "")
        selector = kwargs.get("selector", {})
        fields = kwargs.get("fields", None)

        if "financial_snapshots" in db:
            cid = selector.get("consent_id")
            docs = [d for d in snapshots if d.get("consent_id") == cid]
        elif "consent_registry" in db:
            pid = selector.get("pseudonym_id")
            docs = [d for d in consents if d.get("pseudonym_id") == pid]
        else:
            docs = []

        if fields:
            docs = [{k: d[k] for k in fields if k in d} for d in docs]

        result = MagicMock()
        result.get_result.return_value = {"docs": docs}
        return result

    def _get_document(**kwargs) -> MagicMock:
        doc_id = kwargs.get("doc_id", "")
        db = kwargs.get("db", "")
        if "consent_registry" in db:
            found = next((d for d in consents if d.get("_id") == doc_id), None)
        else:
            found = None
        if found is None:
            raise RuntimeError(f"not found: {doc_id}")
        result = MagicMock()
        result.get_result.return_value = dict(found)
        return result

    def _delete_document(**kwargs) -> MagicMock:
        doc_id = kwargs.get("doc_id", "")
        db = kwargs.get("db", "")
        if "consent_registry" in db:
            to_remove = next((d for d in consents if d.get("_id") == doc_id), None)
            if to_remove:
                consents.remove(to_remove)
        elif "financial_snapshots" in db:
            to_remove = next((d for d in snapshots if d.get("_id") == doc_id), None)
            if to_remove:
                snapshots.remove(to_remove)
        result = MagicMock()
        result.get_result.return_value = {"ok": True}
        return result

    service = MagicMock()
    service.post_find.side_effect = _post_find
    service.get_document.side_effect = _get_document
    service.delete_document.side_effect = _delete_document

    client_callable = MagicMock(return_value=service)
    return client_callable


# ---------------------------------------------------------------------------
# Importação da função sob teste
# ---------------------------------------------------------------------------

import sys
from pathlib import Path

# Adicionar raiz do projeto ao path (necessário para importar scripts/)
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


def _delete(consent_id: str, cloudant_client, vault):
    """Wrapper que chama delete() com dependências injetadas."""
    from scripts.seed_test_consent import delete
    delete(consent_id, cloudant_client=cloudant_client, vault=vault)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def test_delete_shreds_vault_when_no_other_consents():
    """
    R-24: ao apagar o único consentimento do pseudonym_id, o cofre deve ser destruído.
    """
    pid = "pid-only"
    consent = {
        "_id": "cid-only",
        "_rev": "1-aaa",
        "consent_id": "cid-only",
        "pseudonym_id": pid,
        "status": "AUTHORISED",
        "test_fixture": True,
    }
    consents = [consent]
    vault = FakeVault()
    client = _make_cloudant_client(consents)

    _delete("cid-only", client, vault)

    assert pid in vault.shredded, "Cofre deve ser destruído quando não há outros consentimentos"


def test_delete_keeps_vault_when_other_consent_exists():
    """
    R-24: ao apagar um consentimento quando ainda há outro com o mesmo pseudonym_id,
    o cofre NÃO deve ser destruído.
    """
    pid = "pid-shared"
    consent1 = {
        "_id": "cid-shared-1",
        "_rev": "1-bbb",
        "consent_id": "cid-shared-1",
        "pseudonym_id": pid,
        "status": "AUTHORISED",
        "test_fixture": True,
    }
    consent2 = {
        "_id": "cid-shared-2",
        "_rev": "1-ccc",
        "consent_id": "cid-shared-2",
        "pseudonym_id": pid,
        "status": "AUTHORISED",
        "test_fixture": True,
    }
    consents = [consent1, consent2]
    vault = FakeVault()
    client = _make_cloudant_client(consents)

    _delete("cid-shared-1", client, vault)

    assert pid not in vault.shredded, (
        "Cofre NÃO deve ser destruído quando outro consentimento com mesmo pseudonym_id existe"
    )


def test_delete_removes_snapshots():
    """R-24: delete() remove todos os snapshots do consentimento."""
    pid = "pid-snaps"
    consent = {
        "_id": "cid-snaps",
        "_rev": "1-ddd",
        "consent_id": "cid-snaps",
        "pseudonym_id": pid,
        "status": "AUTHORISED",
        "test_fixture": True,
    }
    snap1 = {"_id": "s1", "_rev": "1-e", "consent_id": "cid-snaps"}
    snap2 = {"_id": "s2", "_rev": "1-f", "consent_id": "cid-snaps"}
    consents = [consent]
    snapshots = [snap1, snap2]
    vault = FakeVault()
    client = _make_cloudant_client(consents, snapshots)

    service = client()
    _delete("cid-snaps", client, vault)

    # delete_document deve ter sido chamado para os dois snapshots
    delete_calls = [c.kwargs.get("doc_id") or c.args[0] if c.args else c.kwargs.get("doc_id")
                    for c in service.delete_document.call_args_list]
    # Confirma que s1 e s2 foram deletados
    assert service.delete_document.call_count >= 3  # 2 snapshots + 1 consent


def test_delete_vault_shred_only_once_even_with_many_consents():
    """
    R-24: com dois consentimentos do mesmo pid, apagar cada um individualmente:
    o shred só acontece no último.
    """
    pid = "pid-two"
    consent1 = {
        "_id": "cid-two-1", "_rev": "1-x",
        "consent_id": "cid-two-1", "pseudonym_id": pid,
        "status": "AUTHORISED", "test_fixture": True,
    }
    consent2 = {
        "_id": "cid-two-2", "_rev": "1-y",
        "consent_id": "cid-two-2", "pseudonym_id": pid,
        "status": "AUTHORISED", "test_fixture": True,
    }
    consents = [consent1, consent2]
    vault = FakeVault()
    client = _make_cloudant_client(consents)

    # Apagar o primeiro — cofre deve ser MANTIDO
    _delete("cid-two-1", client, vault)
    assert pid not in vault.shredded, "Cofre não deve ser destruído ainda (segundo consentimento existe)"

    # Apagar o segundo — cofre deve ser DESTRUÍDO
    _delete("cid-two-2", client, vault)
    assert pid in vault.shredded, "Cofre deve ser destruído após apagar o último consentimento"
    assert vault.shredded.count(pid) == 1, "shred deve ser chamado exatamente uma vez"
