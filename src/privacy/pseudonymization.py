"""
Pseudonimização determinística — HMAC-SHA256 com chave mestra segregada.

Módulo compartilhado:
    M1 → gera o pseudonym_id do CPF ao criar o consentimento
    M2 → pseudonimiza identificadores de contrato antes de persistir o snapshot
    M4 → é dono da gestão da chave (produção: Secrets Manager + HSM;
         hackathon: COS + envelope encryption — ver docs/infra-setup.md)

Por que HMAC e não hash puro: o espaço de CPFs tem só 10^11 valores, então
SHA-256(cpf) é revertido por força bruta em minutos. Com HMAC, sem a chave
mestra não há como recalcular o pseudônimo.

A chave é lida de PSEUDONYM_HMAC_KEY (hex). Em produção (APP_ENV=production)
a ausência da chave é erro fatal; fora dela usamos uma chave efêmera de
desenvolvimento, gerada por processo, para que nada rode com chave fixa no código.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from functools import lru_cache

logger = logging.getLogger(__name__)

_CPF_DIGITS = re.compile(r"\D")


class PseudonymizationKeyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _master_key() -> bytes:
    raw = os.getenv("PSEUDONYM_HMAC_KEY")
    if raw:
        try:
            key = bytes.fromhex(raw)
        except ValueError as exc:
            raise PseudonymizationKeyError("PSEUDONYM_HMAC_KEY deve estar em hexadecimal") from exc
        if len(key) < 32:
            raise PseudonymizationKeyError("PSEUDONYM_HMAC_KEY deve ter ao menos 256 bits")
        return key

    if os.getenv("APP_ENV", "development") == "production":
        raise PseudonymizationKeyError("PSEUDONYM_HMAC_KEY ausente em produção")

    logger.warning(
        "PSEUDONYM_HMAC_KEY não definida — usando chave efêmera de desenvolvimento. "
        "Pseudônimos NÃO serão estáveis entre reinícios."
    )
    return secrets.token_bytes(32)


def _hmac_hex(message: str) -> str:
    return hmac.new(_master_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_cpf(cpf: str) -> str:
    digits = _CPF_DIGITS.sub("", cpf)
    if len(digits) != 11:
        raise ValueError("CPF deve conter 11 dígitos")
    return digits


def pseudonymize_cpf(cpf: str) -> str:
    """pseudonym_id do titular. Domínio separado evita colisão com outros usos da chave."""
    return _hmac_hex(f"cpf:{normalize_cpf(cpf)}")


def pseudonymize_contract(institution: str, contract_id: str) -> str:
    """
    Referência opaca e estável de um contrato. Permite correlacionar o mesmo
    contrato entre snapshots sem guardar o número real do contrato.
    """
    return _hmac_hex(f"contract:{institution.lower()}:{contract_id}")[:32]
