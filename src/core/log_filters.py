"""
Filtros de log da aplicação.

CpfMaskingFilter mascara CPFs nos registros do access log do uvicorn. O access
log grava o caminho e a query da URL; se alguém puser um CPF na URL por engano
(ex.: /internal/erase/<cpf>, que a API rejeita), ele não pode ir para o log.
"""

from __future__ import annotations

import logging

from src.privacy.pii_guard import mask_cpfs

ACCESS_LOGGER_NAMES = ("uvicorn.access",)


class CpfMaskingFilter(logging.Filter):
    """Mascara CPFs na mensagem e nos argumentos de formatação do registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = mask_cpfs(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(mask_cpfs(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: mask_cpfs(v) if isinstance(v, str) else v for k, v in record.args.items()}
        except Exception:  # pragma: no cover - nunca derrubar o log
            pass
        return True


def install_access_log_filter() -> None:
    """Instala o filtro nos loggers de acesso (idempotente)."""
    for name in ACCESS_LOGGER_NAMES:
        target = logging.getLogger(name)
        if not any(isinstance(f, CpfMaskingFilter) for f in target.filters):
            target.addFilter(CpfMaskingFilter())
