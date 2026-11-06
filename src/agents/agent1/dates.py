"""Compatibilidade: as datas ISO 8601 vivem em src.core.dates (compartilhadas com o M3)."""

from src.core.dates import parse_utc

__all__ = ["parse_utc"]
