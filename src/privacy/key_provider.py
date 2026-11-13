"""
Abstração de provedor de chaves criptográficas (M4).

Interface `KeyProvider` (Protocol):
    get(purpose, version=None) -> (version_str, key_bytes)

Implementações:
    EnvKeyProvider    — lê do ambiente; adequado para hackathon e testes.

Produção:
    O fluxo de produção deve implementar um KeyProvider sobre o IBM Cloud
    Secrets Manager com HSM dedicado e Trusted Profile IAM (sem API key em
    texto plano). O provider autentica via `ibm-platform-services` (IAM),
    obtém o segredo pelo nome `desendividai/<purpose>/v<N>`, mantém um cache
    curto (< 5 min) para reduzir latência e suporta rotação sem reinício:
    incrementar PSEUDONYM_KEY_ACTIVE_VERSION ou VAULT_KEK_ACTIVE_VERSION faz
    o provider carregar a nova versão na próxima chamada após o cache expirar.
    O role IAM necessário é `SecretsReader` no grupo de recursos do projeto.
    Nunca armazenar chaves no COS — o texto "COS + envelope encryption" em
    docs anteriores está desatualizado (ver docs/m4-chaves-e-iam.md).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

Purpose = Literal["pseudonym", "vault_kek"]


class KeyProviderError(RuntimeError):
    """Chave ausente, inválida ou inacessível."""


class KeyProvider(Protocol):
    def get(
        self,
        purpose: Purpose,
        version: str | None = None,
    ) -> tuple[str, bytes]:
        """
        Retorna (version_str, key_bytes).

        Se `version` for None, retorna a versão ativa.
        Lança `KeyProviderError` se a chave não puder ser obtida.
        """
        ...  # pragma: no cover

    # Opcional: list_versions(purpose) -> list[str]. Usado pelo apagamento por
    # CPF para cobrir pseudônimos emitidos por versões antigas da chave.
    # Providers sem o método são tratados como tendo só a versão ativa.


# ---------------------------------------------------------------------------
# EnvKeyProvider
# ---------------------------------------------------------------------------

class EnvKeyProvider:
    """
    Lê chaves do ambiente.

    Mapeamento de variáveis:
        purpose="pseudonym", version="v1"  → PSEUDONYM_HMAC_KEY
        purpose="pseudonym", version="vN"  → PSEUDONYM_HMAC_KEY_VN  (N >= 2)
        purpose="vault_kek", version="vN"  → VAULT_KEK_VN

    Versão ativa:
        purpose="pseudonym" → PSEUDONYM_KEY_ACTIVE_VERSION (padrão "v1")
        purpose="vault_kek" → VAULT_KEK_ACTIVE_VERSION     (padrão "v1")

    Requisito: hex válido, mínimo 32 bytes (256 bits).

    Cache interno por (purpose, version) para evitar leitura repetida do
    ambiente. Exposto via _reset_key_cache() para uso exclusivo em testes.
    """

    _ACTIVE_VERSION_VARS: dict[str, str] = {
        "pseudonym": "PSEUDONYM_KEY_ACTIVE_VERSION",
        "vault_kek": "VAULT_KEK_ACTIVE_VERSION",
    }

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], bytes] = {}

    def _reset_key_cache(self) -> None:
        """Limpa o cache interno. USO EXCLUSIVO EM TESTES."""
        self._cache.clear()

    def _active_version(self, purpose: Purpose) -> str:
        return os.getenv(self._ACTIVE_VERSION_VARS[purpose], "v1")

    def _env_var_name(self, purpose: Purpose, version: str) -> str:
        if purpose == "pseudonym":
            return "PSEUDONYM_HMAC_KEY" if version == "v1" else f"PSEUDONYM_HMAC_KEY_{version.upper()}"
        # vault_kek
        return f"VAULT_KEK_{version.upper()}"

    def list_versions(self, purpose: Purpose) -> list[str]:
        """
        Versões com chave configurada no ambiente, mais a ativa, em ordem
        numérica (v1, v2, ...). Não lê nem valida o conteúdo das chaves.
        """
        if purpose == "pseudonym":
            pattern = re.compile(r"^PSEUDONYM_HMAC_KEY_V(\d+)$")
            versions = {"v1"} if os.getenv("PSEUDONYM_HMAC_KEY") else set()
        else:
            pattern = re.compile(r"^VAULT_KEK_V(\d+)$")
            versions = set()
        for name, value in os.environ.items():
            match = pattern.match(name)
            if match and value:
                versions.add(f"v{int(match.group(1))}")
        versions.add(self._active_version(purpose))

        def _order(v: str) -> tuple[int, str]:
            digits = v[1:] if v.lower().startswith("v") else ""
            return (int(digits), v) if digits.isdigit() else (10**9, v)

        return sorted(versions, key=_order)

    def get(self, purpose: Purpose, version: str | None = None) -> tuple[str, bytes]:
        resolved = version or self._active_version(purpose)
        cache_key = (purpose, resolved)
        if cache_key in self._cache:
            return resolved, self._cache[cache_key]

        var = self._env_var_name(purpose, resolved)
        raw = os.getenv(var)

        if not raw:
            if purpose == "pseudonym" and resolved == "v1" and os.getenv("APP_ENV", "development") != "production":
                # Preserva o comportamento original: chave efêmera em dev com aviso.
                logger.warning(
                    "%s não definida — usando chave efêmera de desenvolvimento. "
                    "Pseudônimos NÃO serão estáveis entre reinícios.",
                    var,
                )
                key = secrets.token_bytes(32)
                self._cache[cache_key] = key
                return resolved, key
            raise KeyProviderError(
                f"Chave ausente: {var} (purpose={purpose!r}, version={resolved!r}). "
                "Preencha o .env (ver .env.example e docs/m4-chaves-e-iam.md)."
            )

        try:
            key = bytes.fromhex(raw)
        except ValueError as exc:
            raise KeyProviderError(f"{var} deve estar em hexadecimal") from exc

        if purpose == "vault_kek":
            if len(key) != 32:
                raise KeyProviderError(
                    f"{var} deve ter exatamente 256 bits (32 bytes) para uso como KEK AES-256-GCM; "
                    f"comprimento atual: {len(key)} bytes"
                )
        elif len(key) < 32:
            raise KeyProviderError(f"{var} deve ter ao menos 256 bits (32 bytes)")

        self._cache[cache_key] = key
        return resolved, key


# ---------------------------------------------------------------------------
# Instância global padrão
# ---------------------------------------------------------------------------

#: Provider padrão — substituível em testes injetando um provider diferente.
default_provider: KeyProvider = EnvKeyProvider()
