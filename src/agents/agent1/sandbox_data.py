"""
Dados fictícios para desenvolvimento local e demo do hackathon.

Usados automaticamente quando OPEN_FINANCE_BASE_URL / SERASA_BASE_URL /
CLOUDANT_URL não estão configurados. Nenhum dado aqui é real.

Cenário: cliente com 5 contratos em 4 instituições, das quais 3 consentidas
(bb, itau, nubank), e 2 restrições no Serasa — uma do cartão Nubank e outra
de um crediário de varejo que não está no Open Finance.
"""

from datetime import datetime, timedelta, timezone

SANDBOX_CONSENT_ID = "00000000-0000-4000-8000-000000000001"
SANDBOX_REVOKED_CONSENT_ID = "00000000-0000-4000-8000-000000000002"
SANDBOX_CPF = "12345678909"


def sandbox_consents(pseudonym_id: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    base = {
        "pseudonym_id": pseudonym_id,
        "institutions": ["bb", "itau", "nubank"],
        "scopes": ["debts:read", "simulation:read", "recommendation:read"],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=90)).isoformat(),
    }
    return [
        {**base, "_id": SANDBOX_CONSENT_ID, "consent_id": SANDBOX_CONSENT_ID, "status": "AUTHORISED"},
        {**base, "_id": SANDBOX_REVOKED_CONSENT_ID, "consent_id": SANDBOX_REVOKED_CONSENT_ID, "status": "REVOKED"},
    ]


OPEN_FINANCE_CONTRACTS = [
    {
        "institution": "itau",
        "contractId": "ITAU-CP-2025-000123",
        "productType": "EMPRESTIMOS",
        "productSubType": "CREDITO_PESSOAL_SEM_CONSIGNACAO",
        "contractDate": "2026-03-10",
        "firstInstalmentDueDate": "2026-04-10",
        "contractAmount": "10000.00",
        "CET": "0.5713",
        "interestRates": [{"taxPeriodicity": "AM", "preFixedRate": "0.035"}],
        "contractedFees": [
            {"feeName": "TARIFA_CADASTRO", "feeAmount": "200.00"},
            {"feeName": "IOF", "feeAmount": "150.00"},
        ],
        "instalments": {
            "totalNumberOfInstalments": 24, "paidInstalments": 8,
            "dueInstalments": 16, "pastDueInstalments": 0, "instalmentAmount": "622.73",
        },
        "payments": {"contractOutstandingBalance": "7531.50"},
    },
    {
        "institution": "bb",
        "contractId": "BB-CONSIG-2024-998877",
        "productType": "EMPRESTIMOS",
        "productSubType": "CREDITO_PESSOAL_COM_CONSIGNACAO",
        "contractDate": "2025-03-05",
        "firstInstalmentDueDate": "2025-04-05",
        "contractAmount": "15000.00",
        "CET": "0.2441",
        "interestRates": [{"taxPeriodicity": "AM", "preFixedRate": "0.018"}],
        "contractedFees": [{"feeName": "IOF", "feeAmount": "120.00"}],
        "instalments": {
            "totalNumberOfInstalments": 72, "paidInstalments": 20,
            "dueInstalments": 52, "pastDueInstalments": 0, "instalmentAmount": "373.55",
        },
        "payments": {"contractOutstandingBalance": "12543.00"},
    },
    {
        "institution": "nubank",
        "contractId": "NU-CC-5521-XXXX-0042",
        "productType": "CARTAO_CREDITO",
        "productSubType": "CARTAO_CREDITO_ROTATIVO",
        "interestRates": [{"taxPeriodicity": "AM", "preFixedRate": "0.135"}],
        "bill": {"billTotalAmount": "4380.00", "billMinimumAmount": "657.00"},
        "daysOverdue": 45,
    },
    {
        "institution": "itau",
        "contractId": "ITAU-CHESP-0001-77",
        "productType": "ADIANTAMENTO_DEPOSITANTES",
        "productSubType": "CHEQUE_ESPECIAL",
        "interestRates": [{"taxPeriodicity": "AM", "preFixedRate": "0.079"}],
        "payments": {"contractOutstandingBalance": "1850.00"},
    },
    {
        "institution": "bb",
        "contractId": "BB-VEIC-2025-445566",
        "productType": "FINANCIAMENTOS",
        "productSubType": "AQUISICAO_BENS_VEICULOS_AUTOMOTORES",
        "contractDate": "2025-10-20",
        "firstInstalmentDueDate": "2025-11-20",
        "contractAmount": "45000.00",
        "CET": "0.2271",
        "interestRates": [{"taxPeriodicity": "AM", "preFixedRate": "0.016"}],
        "contractedFees": [
            {"feeName": "TARIFA_AVALIACAO_BEM", "feeAmount": "450.00"},
            {"feeName": "IOF", "feeAmount": "450.00"},
        ],
        "instalments": {
            "totalNumberOfInstalments": 48, "paidInstalments": 12,
            "dueInstalments": 36, "pastDueInstalments": 0, "instalmentAmount": "1350.60",
        },
        "payments": {"contractOutstandingBalance": "36720.00"},
    },
    {
        # Fora do consentimento — deve ser filtrado.
        "institution": "santander",
        "contractId": "SAN-CP-2026-1111",
        "productType": "EMPRESTIMOS",
        "productSubType": "CREDITO_PESSOAL_SEM_CONSIGNACAO",
        "contractDate": "2026-01-15",
        "firstInstalmentDueDate": "2026-02-15",
        "contractAmount": "5000.00",
        "interestRates": [{"taxPeriodicity": "AM", "preFixedRate": "0.04"}],
        "instalments": {
            "totalNumberOfInstalments": 12, "paidInstalments": 9,
            "dueInstalments": 3, "pastDueInstalments": 0, "instalmentAmount": "532.76",
        },
        "payments": {"contractOutstandingBalance": "1478.00"},
    },
]

SERASA_REPORT = {
    "score_band": "C",
    "negative_records": [
        {
            "creditor": "nubank",
            "nature": "CARTAO_CREDITO",
            "contract": "NU-CC-5521-XXXX-0042",
            "amount": "4380.00",
            "occurrence_date": "2026-09-19",
        },
        {
            "creditor": "lojas-exemplo",
            "nature": "EMPRESTIMO",
            "contract": "LJX-CREDIARIO-889900",
            "amount": "890.00",
            "occurrence_date": "2026-05-20",
        },
    ],
}
