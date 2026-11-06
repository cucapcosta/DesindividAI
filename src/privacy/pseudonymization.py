"""
Pseudonimização determinística — HMAC-SHA256 com chave mestra segregada.

Módulo compartilhado:
    M1 → gera o pseudonym_id do CPF ao criar o consentimento
    M2 → pseudonimiza identificadores de contrato antes de persistir o snapshot
    M4 → é dono da gestão da chave (produção: Secrets Manager + HSM;
         hackathon: variável de ambiente via EnvKeyProvider — ver
         docs/m4-chaves-e-iam.md)

Por que HMAC e não hash puro: o espaço de CPFs tem só 10^11 valores, então
SHA-256(cpf) é revertido por força bruta em minutos. Com HMAC, sem a chave
mestra não há como recalcular o pseudônimo.

A chave é obtida via `KeyProvider` (padrão: `EnvKeyProvider`, que lê
PSEUDONYM_HMAC_KEY em hex). Em produção (APP_ENV=production) a ausência da
chave é erro fatal; fora dela usamos uma chave efêmera de desenvolvimento,
gerada por processo, para que nada rode com chave fixa no código.

Compatibilidade: `pseudonymize_cpf()` e `pseudonymize_contract()` mantêm
a mesma assinatura e a mesma saída que tinham antes do M4. A versão da chave
fica disponível via `current_key_version()` e
`pseudonymize_cpf_with_version()`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from functools import lru_cache

_CPF_DIGITS = re.compile(r"\D")


# ---------------------------------------------------------------------------
# Acesso ao provider de chaves
# ---------------------------------------------------------------------------

def _get_provider():
    """Retorna o provider de chaves ativo (importação lazy para evitar ciclos)."""
    from .key_provider import default_provider
    return default_provider


@lru_cache(maxsize=8)
def _master_key_cached(version: str) -> bytes:
    """Cache de chave keyed pela versão — invalida automaticamente ao mudar de versão."""
    from .key_provider import KeyProviderError
    try:
        _, key = _get_provider().get("pseudonym", version)
    except KeyProviderError as exc:
        raise PseudonymizationKeyError(str(exc)) from exc
    return key


def _active_version() -> str:
    """Versão ativa de pseudonimização segundo o provider."""
    from .key_provider import KeyProviderError
    try:
        version, _ = _get_provider().get("pseudonym")
    except KeyProviderError as exc:
        raise PseudonymizationKeyError(str(exc)) from exc
    return version


def _master_key() -> bytes:
    """
    Retorna a chave ativa de pseudonimização, keyed pela versão ativa.

    A versão é consultada no provider a cada chamada (o provider tem seu próprio
    cache interno por (purpose, version)); a chave em bytes fica no lru_cache
    de _master_key_cached keyed pela string de versão — assim, ao trocar a versão
    ativa (monkeypatch env + reset do provider), o cache por versão antiga não
    interfere: a nova versão simplesmente não está em cache e é lida do provider.
    """
    ver = _active_version()
    return _master_key_cached(ver)


def current_key_version() -> str:
    """Versão da chave de pseudonimização atualmente ativa."""
    return _active_version()


def reset_key_cache() -> None:
    """Invalida todos os caches de chave (lru_cache + provider interno)."""
    _master_key_cached.cache_clear()
    provider = _get_provider()
    if hasattr(provider, "_reset_key_cache"):
        provider._reset_key_cache()


class PseudonymizationKeyError(RuntimeError):
    pass


def _hmac_hex(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_cpf(cpf: str) -> str:
    digits = _CPF_DIGITS.sub("", cpf)
    if len(digits) != 11:
        raise ValueError("CPF deve conter 11 dígitos")
    return digits


def pseudonymize_cpf(cpf: str) -> str:
    """pseudonym_id do titular. Domínio separado evita colisão com outros usos da chave."""
    return _hmac_hex(_master_key(), f"cpf:{normalize_cpf(cpf)}")


def pseudonymize_cpf_with_version(cpf: str) -> tuple[str, str]:
    """
    Retorna (pseudonym_id, key_version).

    Use quando o documento precisar registrar qual versão da chave gerou o
    pseudônimo (campo `key_version` no identity_vault e nos snapshots novos).

    Usa o mesmo caminho de chave que pseudonymize_cpf (via _master_key / provider)
    para garantir consistência após rotação.
    """
    version = _active_version()
    key = _master_key_cached(version)
    return _hmac_hex(key, f"cpf:{normalize_cpf(cpf)}"), version


def pseudonymize_contract(institution: str, contract_id: str) -> str:
    """
    Referência opaca e estável de um contrato. Permite correlacionar o mesmo
    contrato entre snapshots sem guardar o número real do contrato.
    """
    return _hmac_hex(_master_key(), f"contract:{institution.lower()}:{contract_id}")[:32]
