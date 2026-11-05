"""Datas ISO 8601 vindas de outros serviços (consent_registry, M1)."""

from datetime import datetime, timezone


def parse_utc(value: str) -> datetime:
    """
    Converte ISO 8601 em datetime com fuso. Sem fuso explícito, assume UTC —
    comparar datetime ingênuo com aware levantaria TypeError.
    Levanta ValueError se o valor não for uma data ISO válida.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
