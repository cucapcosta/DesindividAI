"""Configuração via variáveis de ambiente (.env). Lida sob demanda para facilitar testes."""

import os

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


class ConfigurationError(RuntimeError):
    """Variáveis de ambiente obrigatórias ausentes."""


def require_env(*names: str, hint: str = "") -> None:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise ConfigurationError(
            "Variáveis de ambiente ausentes: " + ", ".join(missing)
            + ". Preencha o .env (ver .env.example e docs/infra-setup.md)."
            + (f" {hint}" if hint else "")
        )


WATSONX_VARS = ("WATSONX_URL", "IBM_CLOUD_API_KEY")  # mais WATSONX_SPACE_ID ou WATSONX_PROJECT_ID
CLOUDANT_VARS = ("CLOUDANT_URL", "CLOUDANT_APIKEY")


def watsonx_scope() -> dict[str, str]:
    """Espaço de implementação (tem precedência) ou projeto — usado por todos os agentes."""
    space, project = env("WATSONX_SPACE_ID"), env("WATSONX_PROJECT_ID")
    if space:
        return {"space_id": space}
    if project:
        return {"project_id": project}
    raise ConfigurationError(
        "Variáveis de ambiente ausentes: WATSONX_PROJECT_ID ou WATSONX_SPACE_ID. "
        "Preencha o .env (ver .env.example e docs/infra-setup.md)."
    )


def snapshots_db() -> str:
    return env("CLOUDANT_DB_SNAPSHOTS", "financial_snapshots")


def consents_db() -> str:
    return env("CLOUDANT_DB_CONSENTS", "consent_registry")


def decisions_db() -> str:
    """Banco de simulações, recomendações e traces.

    Contém dados financeiros vinculados ao pseudonym_id e deve ficar em banco
    separado dos snapshots (padrão: ``decision_traces``), com acesso restrito.
    """
    return env("CLOUDANT_DB_DECISIONS", "decision_traces")


def audit_db() -> str:
    """Banco da trilha de auditoria (nunca expurgado)."""
    return env("CLOUDANT_DB_AUDIT", "audit_trail")


def vault_db() -> str:
    """Banco do cofre de identidade (identity_vault)."""
    return env("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")
