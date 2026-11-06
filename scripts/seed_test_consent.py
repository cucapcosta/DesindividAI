"""
Grava ou remove um consentimento de teste no Cloudant (consent_registry).

Atalho de teste: cria direto um consentimento AUTHORISED (marcado test_fixture)
para exercitar GET /debts contra os serviços reais. O caminho normal é
POST /consent seguido do callback (code=sandbox no modo sandbox).
O CPF é o fictício do sandbox; só o pseudonym_id (HMAC) é persistido.

O pseudonym_id é gerado via identity_vault.store(SANDBOX_CPF), que também
cifra o CPF no cofre de identidade (M4).

Uso (na raiz do repo, com o .env preenchido):
    python scripts/seed_test_consent.py create
    python scripts/seed_test_consent.py delete <consent_id>   # remove consentimento,
                                                              # snapshots e cofre do titular
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from ibmcloudant.cloudant_v1 import Document  # noqa: E402

from src.agents.agent1.repository import build_cloudant_client  # noqa: E402
from src.agents.agent1.sandbox_data import SANDBOX_CPF  # noqa: E402
from src.core.config import consents_db, snapshots_db  # noqa: E402
from src.privacy.identity_vault import get_identity_vault  # noqa: E402

CONSENTS_DB = consents_db()
SNAPSHOTS_DB = snapshots_db()
client = build_cloudant_client


def create() -> None:
    now = datetime.now(timezone.utc)
    consent_id = str(uuid.uuid4())
    # M4: gerar pseudonym_id via cofre de identidade (cifra e armazena o CPF).
    pseudonym_id = get_identity_vault().store(SANDBOX_CPF)
    doc = {
        "_id": consent_id,
        "consent_id": consent_id,
        "pseudonym_id": pseudonym_id,
        "institutions": ["bb", "itau", "nubank"],
        "scopes": ["debts:read", "simulation:read", "recommendation:read"],
        "status": "AUTHORISED",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=90)).isoformat(),
        "test_fixture": True,
    }
    client().put_document(db=CONSENTS_DB, doc_id=consent_id, document=Document.from_dict(doc))
    print(consent_id)


def _count_consents_for_pseudonym(service, pseudonym_id: str, exclude_consent_id: str) -> int:
    """Conta consentimentos restantes com o mesmo pseudonym_id (excluindo o que acabou de ser apagado)."""
    result = service.post_find(
        db=CONSENTS_DB,
        selector={"pseudonym_id": pseudonym_id},
        fields=["_id"],
    ).get_result()["docs"]
    return sum(1 for d in result if d["_id"] != exclude_consent_id)


def delete(
    consent_id: str,
    *,
    cloudant_client=None,
    vault=None,
) -> None:
    """Remove um consentimento de teste e, opcionalmente, o cofre do titular.

    O cofre só é destruído se não restar nenhum outro consentimento com o mesmo
    pseudonym_id após a remoção deste.

    Parâmetros opcionais para injeção em testes:
        cloudant_client — callable que retorna o cliente Cloudant (padrão: client)
        vault           — IdentityVault já instanciado (padrão: get_identity_vault())
    """
    if cloudant_client is None:
        cloudant_client = client
    if vault is None:
        vault = get_identity_vault()

    service = cloudant_client()
    snapshots = service.post_find(
        db=SNAPSHOTS_DB, selector={"consent_id": consent_id}, fields=["_id", "_rev"]
    ).get_result()["docs"]
    for snap in snapshots:
        service.delete_document(db=SNAPSHOTS_DB, doc_id=snap["_id"], rev=snap["_rev"])

    consent = service.get_document(db=CONSENTS_DB, doc_id=consent_id).get_result()
    if not consent.get("test_fixture"):
        sys.exit(f"{consent_id} não foi criado por este script — recusando apagar.")
    pseudonym_id = consent["pseudonym_id"]
    service.delete_document(db=CONSENTS_DB, doc_id=consent_id, rev=consent["_rev"])

    # M4: destruir o cofre do titular APENAS se não restar nenhum outro consentimento
    # com o mesmo pseudonym_id (o SANDBOX_CPF é compartilhado por todos os consentimentos
    # de teste — apagar o cofre enquanto houver outros consentimentos ativos seria incorreto).
    remaining = _count_consents_for_pseudonym(service, pseudonym_id, consent_id)
    if remaining > 0:
        print(
            f"removidos: consentimento {consent_id} e {len(snapshots)} snapshot(s); "
            f"cofre mantido ({remaining} consentimento(s) restante(s) para o mesmo titular)"
        )
    else:
        vault_shredded = vault.shred(pseudonym_id)
        print(
            f"removidos: consentimento {consent_id} e {len(snapshots)} snapshot(s); "
            f"cofre destruído: {vault_shredded}"
        )


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "create":
        create()
    elif len(sys.argv) == 3 and sys.argv[1] == "delete":
        delete(sys.argv[2])
    else:
        sys.exit(__doc__)
