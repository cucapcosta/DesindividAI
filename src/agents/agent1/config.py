"""
Configuração do Agente 1.

O agente sempre usa os serviços reais descritos no PLANO: watsonx.ai (Granite)
e Cloudant. Faltando credenciais, FALHA com mensagem clara.

Open Finance e Serasa não têm credenciais disponíveis na conta do hackathon
(exigem participação regulada no Open Finance e contrato comercial com a
Serasa). Cada fonte pode ser simulada de forma explícita e independente:

    OPEN_FINANCE_SANDBOX=true → contratos fictícios (sandbox_data.py)
    SERASA_SANDBOX=true       → relatório fictício (sandbox_data.py)

O snapshot registra em sources.<fonte>.sandbox quais dados são fictícios.
"""

from __future__ import annotations

import os


class ConfigurationError(RuntimeError):
    """Variáveis de ambiente obrigatórias ausentes."""


def _flag(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def open_finance_sandbox() -> bool:
    return _flag("OPEN_FINANCE_SANDBOX")


def serasa_sandbox() -> bool:
    return _flag("SERASA_SANDBOX")


def require_env(*names: str, hint: str = "") -> None:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise ConfigurationError(
            "Variáveis de ambiente ausentes: " + ", ".join(missing)
            + ". Preencha o .env (ver .env.example e docs/infra-setup.md)."
            + (f" {hint}" if hint else "")
        )


WATSONX_VARS = ("WATSONX_URL", "WATSONX_PROJECT_ID", "IBM_CLOUD_API_KEY")
CLOUDANT_VARS = ("CLOUDANT_URL", "CLOUDANT_APIKEY")
