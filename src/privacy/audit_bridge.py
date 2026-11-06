"""
Ponte async → sync entre o M3 (código async) e a trilha de auditoria do M4 (SDK síncrono).

Regras:
- Nenhuma função aqui levanta exceção — falhas de auditoria são logadas e ignoradas.
- _get_logger() é uma função separada para permitir monkeypatch nos testes.
- emit() chama record_event via asyncio.to_thread para não bloquear o event loop.
- guard_or_raise() verifica PII e lança ServiceError(500) se encontrar; também dispara
  um evento audit.pii_blocked de forma fire-and-forget síncrona e tolerante.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Padrão: 64 caracteres hexadecimais (pseudonym_id HMAC-SHA256)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _get_logger():
    """
    Obtém o AuditLogger do M4.

    Retorna None (sem propagar) se get_audit_logger() lançar qualquer exceção
    (ConfigurationError, ImportError, etc.). Função separada para monkeypatch nos testes.
    """
    try:
        from src.privacy.audit import get_audit_logger
        return get_audit_logger()
    except Exception as exc:
        logger.warning(
            "audit_bridge: auditoria indisponível. exc=%s",
            type(exc).__name__,
        )
        return None


async def emit(
    event_type: str,
    *,
    pseudonym_id: str | None,
    actor: str,
    **fields: Any,
) -> str | None:
    """
    Emite um evento de auditoria de forma async-tolerante.

    Retorna o trace_id retornado por record_event, ou None se a auditoria
    estiver indisponível ou lançar exceção.
    Nunca propaga exceção.
    """
    audit_logger = _get_logger()
    if audit_logger is None:
        return None
    try:
        return await asyncio.to_thread(
            audit_logger.record_event,
            event_type,
            pseudonym_id=pseudonym_id,
            actor=actor,
            **fields,
        )
    except Exception as exc:
        logger.warning(
            "audit_bridge.emit: falha ao emitir evento event_type=%s exc=%s",
            event_type,
            type(exc).__name__,
        )
        return None


def _emit_sync_fire_and_forget(
    event_type: str,
    *,
    pseudonym_id: str | None,
    actor: str,
    **fields: Any,
) -> None:
    """
    Dispara um evento de auditoria de forma síncrona e fire-and-forget.
    Qualquer exceção é suprimida. Usado internamente por guard_or_raise.
    """
    audit_logger = _get_logger()
    if audit_logger is None:
        return
    try:
        audit_logger.record_event(
            event_type,
            pseudonym_id=pseudonym_id,
            actor=actor,
            **fields,
        )
    except Exception as exc:
        logger.warning(
            "audit_bridge._emit_sync_fire_and_forget: falha. event_type=%s exc=%s",
            event_type,
            type(exc).__name__,
        )


def guard_or_raise(doc: dict, *, context: str) -> None:
    """
    Chama assert_no_pii(doc). Se detectar PIIViolation:
    - Emite evento "audit.pii_blocked" com blocked_event_type=context e
      violation_path=exc.path (fire-and-forget, tolerante).
    - Levanta ServiceError(500, "PII_BLOCKED", ...) com mensagem genérica
      (nunca contém o valor violador).

    Retorna None se o documento estiver limpo.
    """
    from src.core.errors import ServiceError
    from src.privacy.pii_guard import PIIViolation, assert_no_pii

    try:
        assert_no_pii(doc)
    except PIIViolation as exc:
        # Extrair pseudonym_id do doc se for string hexadecimal de 64 chars
        raw_pid = doc.get("pseudonym_id") if isinstance(doc, dict) else None
        safe_pid: str | None = (
            raw_pid
            if isinstance(raw_pid, str) and _HEX64_RE.match(raw_pid)
            else None
        )
        _emit_sync_fire_and_forget(
            "audit.pii_blocked",
            pseudonym_id=safe_pid,
            actor="agent2",
            blocked_event_type=context,
            violation_path=exc.path,
        )
        raise ServiceError(
            500,
            "PII_BLOCKED",
            "Dados pessoais detectados; operação bloqueada.",
        ) from exc


def sha256_text(text: str) -> str:
    """Retorna o hash SHA-256 (hex) do texto em UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
