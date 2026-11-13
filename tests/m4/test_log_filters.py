"""
Access log do uvicorn não pode gravar CPF colocado na URL (achado do E2E com servidor real).
"""

from __future__ import annotations

import logging

import pytest

from src.core.log_filters import CpfMaskingFilter, install_access_log_filter
from src.privacy.pii_guard import mask_cpfs

_CPF = "52998224725"


@pytest.mark.parametrize("text,expected", [
    (f"/internal/erase/{_CPF}", "/internal/erase/<cpf>"),
    ("/x?cpf=529.982.247-25", "/x?cpf=<cpf>"),
    ("/x/529 982 247 25/y", "/x/<cpf>/y"),
    ("/x/52998224726", "/x/52998224726"),        # DV inválido: não é CPF
    ("/debts?consent_id=1234", "/debts?consent_id=1234"),
    ("sem nada", "sem nada"),
])
def test_mask_cpfs(text, expected):
    assert mask_cpfs(text) == expected


def _access_record(path: str) -> logging.LogRecord:
    # Mesmo formato do uvicorn.logging.AccessFormatter / h11_impl
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "POST", path, "1.1", 401), None,
    )


def test_filter_masks_access_log_args():
    record = _access_record(f"/internal/erase/{_CPF}")
    assert CpfMaskingFilter().filter(record) is True
    assert _CPF not in record.getMessage()
    assert "/internal/erase/<cpf>" in record.getMessage()
    assert record.args[4] == 401  # não-strings intactos


def test_filter_masks_dict_args_and_msg():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               f"cpf {_CPF} %(p)s", None, None)
    record.args = {"p": f"/a/{_CPF}"}
    CpfMaskingFilter().filter(record)
    assert _CPF not in record.getMessage()


def test_install_is_idempotent_and_applies_to_uvicorn_access():
    install_access_log_filter()
    install_access_log_filter()
    filters = [f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, CpfMaskingFilter)]
    assert len(filters) == 1


def test_app_import_installs_filter():
    import src.main  # noqa: F401

    assert any(isinstance(f, CpfMaskingFilter) for f in logging.getLogger("uvicorn.access").filters)


def test_real_uvicorn_access_logger_output_is_masked(caplog):
    """Passa o registro pelo logger real e pelo formatter de acesso do uvicorn."""
    from uvicorn.logging import AccessFormatter

    install_access_log_filter()
    access = logging.getLogger("uvicorn.access")
    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            lines.append(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',
                                         use_colors=False).format(record))

    handler = _H()
    old_level, old_disabled = access.level, access.disabled
    access.addHandler(handler)
    access.setLevel(logging.INFO)
    access.disabled = False
    try:
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "POST", f"/internal/erase/{_CPF}", "1.1", 401)
    finally:
        access.removeHandler(handler)
        access.setLevel(old_level)
        access.disabled = old_disabled
    assert lines and _CPF not in lines[0] and "<cpf>" in lines[0]
