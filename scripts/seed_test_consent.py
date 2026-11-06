"""
Grava ou remove um consentimento de teste no Cloudant (consent_registry).

Enquanto POST /consent (M1) não está implementado, este script cria um
consentimento AUTHORISED para exercitar GET /debts contra os serviços reais.
O CPF é o fictício do sandbox; só o pseudonym_id (HMAC) é persistido.

Uso (na raiz do repo, com o .env preenchido):
    python scripts/seed_test_consent.py create
    python scripts/seed_test_consent.py delete <consent_id>   # remove consentimento e snapshots dele
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
from src.privacy.pseudonymization import pseudonymize_cpf  # noqa: E402

CONSENTS_DB = consents_db()
SNAPSHOTS_DB = snapshots_db()
client = build_cloudant_client


def create() -> None:
    now = datetime.now(timezone.utc)
    consent_id = str(uuid.uuid4())
    doc = {
        "_id": consent_id,
        "consent_id": consent_id,
        "pseudonym_id": pseudonymize_cpf(SANDBOX_CPF),
        "institutions": ["bb", "itau", "nubank"],
        "scopes": ["debts:read", "simulation:read", "recommendation:read"],
        "status": "AUTHORISED",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=90)).isoformat(),
        "test_fixture": True,
    }
    client().put_document(db=CONSENTS_DB, doc_id=consent_id, document=Document.from_dict(doc))
    print(consent_id)


def delete(consent_id: str) -> None:
    service = client()
    snapshots = service.post_find(
        db=SNAPSHOTS_DB, selector={"consent_id": consent_id}, fields=["_id", "_rev"]
    ).get_result()["docs"]
    for snap in snapshots:
        service.delete_document(db=SNAPSHOTS_DB, doc_id=snap["_id"], rev=snap["_rev"])

    consent = service.get_document(db=CONSENTS_DB, doc_id=consent_id).get_result()
    if not consent.get("test_fixture"):
        sys.exit(f"{consent_id} não foi criado por este script — recusando apagar.")
    service.delete_document(db=CONSENTS_DB, doc_id=consent_id, rev=consent["_rev"])
    print(f"removidos: consentimento {consent_id} e {len(snapshots)} snapshot(s)")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "create":
        create()
    elif len(sys.argv) == 3 and sys.argv[1] == "delete":
        delete(sys.argv[2])
    else:
        sys.exit(__doc__)
