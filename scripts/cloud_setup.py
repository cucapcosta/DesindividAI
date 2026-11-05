"""
Prepara o Cloudant da conta atual: cria os databases e um snapshot de exemplo.
Idempotente — pode rodar várias vezes.

    python -m scripts.cloud_setup

Requer CLOUDANT_URL e CLOUDANT_APIKEY no .env. O snapshot de exemplo imita o que o
Agente 1 (M2) deve gravar (ver docs/m3-simulation-engine.md) e usa um pseudonym_id fictício.
"""

from __future__ import annotations

import asyncio
import sys

from src.core.config import consents_db, decisions_db, env, snapshots_db
from src.storage.store import CloudantStore

SAMPLE_SNAPSHOT_ID = "00000000-0000-4000-8000-000000000001"

SAMPLE_DEBTS = [
    {"debt_id": "00000000-0000-4000-8000-0000000000a1", "institution": "itau", "product_type": "CREDIT_CARD",
     "outstanding_balance": 5000.0, "cet_annual": 200.0, "monthly_payment": 300.0,
     "remaining_installments": 24, "priority_rank": 1},
    {"debt_id": "00000000-0000-4000-8000-0000000000a2", "institution": "bb", "product_type": "PERSONAL_LOAN",
     "outstanding_balance": 8000.0, "cet_annual": 60.0, "monthly_payment": 400.0,
     "remaining_installments": 24, "priority_rank": 2},
    {"debt_id": "00000000-0000-4000-8000-0000000000a3", "institution": "caixa", "product_type": "VEHICLE_LOAN",
     "outstanding_balance": 12000.0, "cet_annual": 20.0, "monthly_payment": 500.0,
     "remaining_installments": 30, "priority_rank": 3},
]


def cloudant_store() -> CloudantStore:
    url, key = env("CLOUDANT_URL"), env("CLOUDANT_APIKEY")
    if not (url and key):
        sys.exit("CLOUDANT_URL e CLOUDANT_APIKEY precisam estar no .env.")
    return CloudantStore(url, key)


async def setup(store: CloudantStore) -> None:
    dbs = {snapshots_db(), decisions_db(), consents_db()}
    for db in sorted(dbs):
        created = await store.ensure_db(db)
        print(f"[OK] database {db}: {'criado' if created else 'já existia'}")
    await store.put(
        snapshots_db(),
        SAMPLE_SNAPSHOT_ID,
        {
            "type": "snapshot",
            "pseudonym_id": "exemplo-pseudonimizado",
            "snapshot_id": SAMPLE_SNAPSHOT_ID,
            "total_outstanding": sum(d["outstanding_balance"] for d in SAMPLE_DEBTS),
            "generated_at": "2026-01-01T00:00:00+00:00",
            "debts": SAMPLE_DEBTS,
        },
    )
    print(f"[OK] snapshot de exemplo {SAMPLE_SNAPSHOT_ID} gravado em {snapshots_db()}")


if __name__ == "__main__":
    asyncio.run(setup(cloudant_store()))
