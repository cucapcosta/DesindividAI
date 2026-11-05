"""
Atualiza src/agents/agent1/market_rates.json com as taxas médias de juros PF do BCB (SGS).

Essas taxas estimam o CET de dívidas que só aparecem no Serasa (sem taxa contratual).
São taxas de JUROS, não CET: o CET real é maior (inclui IOF e tarifas), então a
estimativa é um piso — conservadora para a priorização.

Uso (na raiz do repo):  python scripts/update_market_rates.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

OUTPUT = Path(__file__).resolve().parents[1] / "src" / "agents" / "agent1" / "market_rates.json"

# ProductType → série SGS "Taxa média de juros das operações de crédito - Pessoas físicas - …" (% a.a.)
SERIES = {
    "CREDIT_CARD": (22022, "Cartão de crédito rotativo"),
    "OVERDRAFT": (20741, "Cheque especial"),
    "PERSONAL_LOAN": (20742, "Crédito pessoal não consignado"),
    "PAYROLL_LOAN": (20747, "Crédito pessoal consignado total"),
    "VEHICLE_LOAN": (20749, "Aquisição de veículos"),
    "MORTGAGE": (20774, "Financiamento imobiliário total"),
}


def latest(code: int) -> dict:
    response = httpx.get(
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados/ultimos/1",
        params={"formato": "json"}, timeout=30, follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()[0]


def main() -> None:
    rates = {}
    for product, (code, label) in SERIES.items():
        point = latest(code)
        reference = datetime.strptime(point["data"], "%d/%m/%Y").date().isoformat()
        rates[product] = {
            "annual_rate_pct": float(point["valor"]),
            "sgs_series": code,
            "series_label": label,
            "reference_month": reference,
        }
        print(f"{product:14} {point['valor']:>8}% a.a.  (SGS {code}, {reference})")

    OUTPUT.write_text(json.dumps({
        "source": "BCB/SGS — taxa média de juros, pessoas físicas (% a.a.)",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "rates": rates,
    }, indent=2, ensure_ascii=False) + "\n")
    print(f"gravado em {OUTPUT}")


if __name__ == "__main__":
    main()
