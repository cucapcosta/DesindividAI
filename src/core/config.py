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
