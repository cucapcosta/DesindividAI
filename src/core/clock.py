"""Helpers de relógio compartilhados — evita duplicação de _utcnow_iso em vários módulos."""

from datetime import datetime, timezone
from typing import Optional


def utcnow_iso() -> str:
    """Instante atual em UTC, formatado como ISO-8601Z (ex.: '2026-11-06T13:00:00Z')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso(dt: Optional[datetime] = None) -> str:
    """
    Converte *dt* para string ISO-8601Z.

    Se *dt* for None, usa o instante atual em UTC.
    """
    effective = dt if dt is not None else datetime.now(timezone.utc)
    return effective.strftime("%Y-%m-%dT%H:%M:%SZ")
