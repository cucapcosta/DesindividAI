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

Validação de ambiente, nomes de bancos e escopo do watsonx são compartilhados
com o M3 em src.core.config; os nomes abaixo são reexportados por compatibilidade.
"""

from __future__ import annotations

from src.core.config import (  # noqa: F401  (reexportados)
    CLOUDANT_VARS,
    WATSONX_VARS,
    ConfigurationError,
    env_bool,
    require_env,
)


def open_finance_sandbox() -> bool:
    return env_bool("OPEN_FINANCE_SANDBOX")


def serasa_sandbox() -> bool:
    return env_bool("SERASA_SANDBOX")
