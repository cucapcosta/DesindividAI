"""
Testes ponta a ponta (integração) — M4, Fase 8.

Requerem credenciais reais no .env. Para rodar apenas os testes unitários:
    pytest -q -m "not integration"

Para rodar este arquivo contra a cloud real:
    pytest -q -m integration

Nunca são executados em CI sem credenciais (fixture real_env faz pytest.skip).
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Marker de módulo — todos os testes deste arquivo requerem a mark integration
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Variáveis do .env que os testes de integração injetam
# ---------------------------------------------------------------------------

_ENV_KEYS = (
    "CLOUDANT_URL",
    "CLOUDANT_APIKEY",
    "CLOUDANT_DB_CONSENTS",
    "CLOUDANT_DB_SNAPSHOTS",
    "COS_ACCESS_KEY_ID",
    "COS_SECRET_ACCESS_KEY",
    "COS_BUCKET_NAME",
    "COS_BUCKET_REGION",
    "WATSONX_URL",
    "WATSONX_PROJECT_ID",
    "WATSONX_MODEL_ID",
    "IBM_CLOUD_API_KEY",
)

# Estas chaves NÃO vêm do .env; permanecem as de teste do conftest.
_KEEP_TEST_KEYS = (
    "PSEUDONYM_HMAC_KEY",
    "VAULT_KEK_V1",
    "VAULT_KEK_ACTIVE_VERSION",
    "PSEUDONYM_KEY_ACTIVE_VERSION",
    "INTERNAL_API_TOKEN",
    "OPEN_FINANCE_SANDBOX",
    "SERASA_SANDBOX",
)


# ---------------------------------------------------------------------------
# Helpers: CPF aleatório válido
# ---------------------------------------------------------------------------

def _gen_random_cpf() -> str:
    """Gera um CPF válido aleatório. Nunca deve ser impresso."""
    rng = random.SystemRandom()
    while True:
        digits = [rng.randint(0, 9) for _ in range(9)]
        if len(set(digits)) == 1:
            continue  # rejeitar todos iguais
        # Primeiro dígito verificador
        total = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
        rem = (total * 10) % 11
        d1 = rem if rem < 10 else 0
        digits.append(d1)
        # Segundo dígito verificador
        total2 = sum(d * w for d, w in zip(digits, range(11, 1, -1)))
        rem2 = (total2 * 10) % 11
        d2 = rem2 if rem2 < 10 else 0
        digits.append(d2)
        cpf = "".join(str(d) for d in digits)
        if len(set(cpf)) > 1:
            return cpf


# ---------------------------------------------------------------------------
# Fixture de módulo: real_env
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_env():
    """
    Lê o .env do repositório, valida credenciais Cloudant e aplica as
    variáveis reais ao ambiente — apenas as listadas em _ENV_KEYS.
    Chaves de teste (HMAC, KEK, TOKEN) permanecem inalteradas.
    Limpa os caches das factories antes e depois.
    """
    from dotenv import dotenv_values

    env_path = Path(__file__).resolve().parents[2] / ".env"
    values = dotenv_values(env_path)

    url = values.get("CLOUDANT_URL", "")
    apikey = values.get("CLOUDANT_APIKEY", "")
    if not url or not apikey:
        pytest.skip("sem credenciais reais (CLOUDANT_URL ou CLOUDANT_APIKEY vazios no .env)")

    def _clear_caches():
        """Limpa todos os lru_cache das factories do M4."""
        from src.agents.agent1.repository import get_repository
        from src.privacy.audit import get_audit_logger, get_audit_repository
        from src.privacy.identity_vault import get_vault_repository
        from src.privacy.pseudonymization import _reset_key_cache
        from src.privacy.retention_store import get_retention_store

        get_repository.cache_clear()
        get_vault_repository.cache_clear()
        get_audit_repository.cache_clear()
        get_audit_logger.cache_clear()
        get_retention_store.cache_clear()
        _reset_key_cache()

        # Limpar dependency_overrides do app
        from src.main import app
        app.dependency_overrides.clear()

    _clear_caches()

    with pytest.MonkeyPatch.context() as mp:
        for key in _ENV_KEYS:
            val = values.get(key)
            if val:
                mp.setenv(key, val)
        yield values

    _clear_caches()

    from src.main import app
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fixture: CPF de teste por execução (aleatório)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_cpf(real_env):
    """CPF válido aleatório para esta execução de testes. Nunca impresso."""
    return _gen_random_cpf()


# ---------------------------------------------------------------------------
# Fixture: cliente Cloudant real
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cloudant_client(real_env):
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
    from ibmcloudant.cloudant_v1 import CloudantV1

    import os
    client = CloudantV1(authenticator=IAMAuthenticator(os.environ["CLOUDANT_APIKEY"]))
    client.set_service_url(os.environ["CLOUDANT_URL"])
    return client


# ---------------------------------------------------------------------------
# Fixture: cleanup — acumula (db, doc_id) e COS keys para teardown
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cleanup(real_env, cloudant_client):
    """
    Acumula documentos Cloudant e objetos COS a apagar no teardown.
    Uso: cleanup.add_doc(db, doc_id); cleanup.add_cos(key)
    """

    class _Cleanup:
        def __init__(self):
            self._docs: list[tuple[str, str]] = []  # (db, doc_id)
            self._cos_keys: list[str] = []
            self._pseudonyms: list[str] = []  # pseudonym_ids para limpar audit_trail

        def add_doc(self, db: str, doc_id: str) -> None:
            self._docs.append((db, doc_id))

        def add_cos(self, key: str) -> None:
            self._cos_keys.append(key)

        def add_pseudonym(self, pid: str) -> None:
            if pid not in self._pseudonyms:
                self._pseudonyms.append(pid)

    c = _Cleanup()
    yield c

    # --- teardown ---
    import os

    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")
    cos_prefix = os.getenv("COS_AUDIT_PREFIX", "audit/")

    # Construir cliente COS uma vez (se disponível) para uso no teardown de auditoria e COS keys
    access_key = os.getenv("COS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("COS_SECRET_ACCESS_KEY", "")
    bucket = os.getenv("COS_BUCKET_NAME", "")
    cos_client = None
    if access_key and secret_key and bucket:
        try:
            import ibm_boto3
            from ibm_botocore.client import Config

            cos_client = ibm_boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(signature_version="s3v4"),
                endpoint_url="https://s3.us-south.cloud-object-storage.appdomain.cloud",
                region_name="us-south",
            )
        except Exception:
            logger.info("cleanup COS: impossível criar cliente COS no teardown")

    # Apagar documentos Cloudant (consents/snapshots — antes do vault e audit)
    for db, doc_id in c._docs:
        try:
            doc = cloudant_client.get_document(db=db, doc_id=doc_id).get_result()
            # Se for documento de auditoria, apagar objeto COS correspondente primeiro
            if db == audit_db and cos_client is not None:
                try:
                    cos_key = _cos_key_for(
                        doc.get("trace_id", doc_id),
                        doc.get("recorded_at", ""),
                        cos_prefix,
                    )
                    cos_client.delete_object(Bucket=bucket, Key=cos_key)
                    logger.info("cleanup COS (add_doc audit): apagado %s", cos_key)
                except Exception:
                    pass
            rev = doc.get("_rev", "")
            cloudant_client.delete_document(db=db, doc_id=doc_id, rev=rev)
            logger.info("cleanup: apagado %s/%s", db, doc_id)
        except Exception:
            logger.info("cleanup: _id=%s já inexistente ou erro ignorado", doc_id)

    # Apagar eventos de auditoria para cada pseudonym registrado
    for pid in c._pseudonyms:
        bookmark = None
        while True:
            try:
                kwargs: dict = dict(
                    db=audit_db,
                    selector={"pseudonym_id": pid},
                    limit=25,
                )
                if bookmark:
                    kwargs["bookmark"] = bookmark
                result = cloudant_client.post_find(**kwargs).get_result()
            except Exception:
                break
            audit_docs = result.get("docs", [])
            for adoc in audit_docs:
                adoc_id = adoc.get("_id", "")
                # Apagar objeto COS correspondente (ignora 404)
                if cos_client is not None:
                    try:
                        cos_key = _cos_key_for(
                            adoc.get("trace_id", adoc_id),
                            adoc.get("recorded_at", ""),
                            cos_prefix,
                        )
                        cos_client.delete_object(Bucket=bucket, Key=cos_key)
                        logger.info("cleanup COS (audit): apagado %s", cos_key)
                    except Exception:
                        pass
                # Apagar documento Cloudant
                try:
                    rev = adoc.get("_rev", "")
                    cloudant_client.delete_document(db=audit_db, doc_id=adoc_id, rev=rev)
                    logger.info("cleanup audit: apagado %s/%s", audit_db, adoc_id)
                except Exception:
                    logger.info("cleanup audit: _id=%s já inexistente ou erro ignorado", adoc_id)
            new_bookmark = result.get("bookmark")
            if not audit_docs or new_bookmark == bookmark:
                break
            bookmark = new_bookmark

    # Apagar objetos COS rastreados explicitamente (add_cos)
    if c._cos_keys and cos_client is not None:
        for key in c._cos_keys:
            try:
                cos_client.delete_object(Bucket=bucket, Key=key)
                logger.info("cleanup COS: apagado %s", key)
            except Exception:
                logger.info("cleanup COS: chave %s já inexistente ou erro ignorado", key)


# ---------------------------------------------------------------------------
# Fixture: vault de identidade real
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_vault(real_env):
    from src.privacy.identity_vault import get_identity_vault
    return get_identity_vault()


# ---------------------------------------------------------------------------
# Fixture: audit logger real
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_audit(real_env):
    from src.privacy.audit import get_audit_logger
    return get_audit_logger()


# ---------------------------------------------------------------------------
# Fixture: retention store real
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_store(real_env):
    from src.privacy.retention_store import get_retention_store
    return get_retention_store()


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _cos_key_for(trace_id: str, recorded_at: str, prefix: str) -> str:
    """Reconstrói a chave COS para um trace_id dado o recorded_at ISO-8601Z."""
    try:
        dt = datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        dt = datetime.now(timezone.utc)
    return f"{prefix}{dt.year:04d}/{dt.month:02d}/{dt.day:02d}/{trace_id}.json"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_iso(days: int = 1) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_iso(days: int = 90) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# a. test_ensure_databases_and_indexes
# ---------------------------------------------------------------------------

def test_ensure_databases_and_indexes(real_env, cloudant_client):
    """
    ensure_databases() e ensure_indexes() são idempotentes.
    Os bancos identity_vault e audit_trail existem após a chamada.
    """
    import os
    from src.privacy.retention_store import ensure_databases, ensure_indexes

    # Primeira chamada
    ensure_databases(cloudant_client)
    ensure_indexes(cloudant_client)

    # Segunda chamada — idempotência
    ensure_databases(cloudant_client)
    ensure_indexes(cloudant_client)

    # Verificar existência
    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")
    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")

    info_vault = cloudant_client.get_database_information(db=vault_db).get_result()
    assert info_vault.get("db_name") == vault_db

    info_audit = cloudant_client.get_database_information(db=audit_db).get_result()
    assert info_audit.get("db_name") == audit_db


# ---------------------------------------------------------------------------
# b. test_vault_roundtrip_real
# ---------------------------------------------------------------------------

def test_vault_roundtrip_real(real_env, test_cpf, real_vault, cloudant_client, cleanup):
    """
    store → documento no Cloudant sem CPF em claro → reveal devolve o CPF
    → shred → reveal falha com VaultError.
    """
    import os
    from src.privacy.identity_vault import VaultError

    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")

    # store
    pseudonym_id = real_vault.store(test_cpf)
    cleanup.add_doc(vault_db, pseudonym_id)
    cleanup.add_pseudonym(pseudonym_id)

    # O documento não deve conter o CPF em claro
    doc = cloudant_client.get_document(db=vault_db, doc_id=pseudonym_id).get_result()
    doc_json = json.dumps(doc)
    from src.privacy.pseudonymization import normalize_cpf
    cpf_normalized = normalize_cpf(test_cpf)
    assert cpf_normalized not in doc_json, "CPF normalizado encontrado no documento Cloudant"

    # reveal retorna o CPF
    revealed = real_vault.reveal(pseudonym_id, purpose="LEGAL_REQUEST", actor="integration_test")
    assert revealed == cpf_normalized

    # shred
    shredded = real_vault.shred(pseudonym_id)
    assert shredded is True

    # reveal depois de shred deve falhar
    with pytest.raises(VaultError):
        real_vault.reveal(pseudonym_id, purpose="LEGAL_REQUEST", actor="integration_test")


# ---------------------------------------------------------------------------
# c. test_audit_cloudant_and_cos
# ---------------------------------------------------------------------------

def test_audit_cloudant_and_cos(real_env, test_cpf, real_vault, real_audit, cloudant_client, cleanup):
    """
    record_event grava em Cloudant (audit_trail) e no COS.
    O documento auditado não contém o CPF.
    """
    import os
    from src.privacy.pseudonymization import pseudonymize_cpf

    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")
    cos_prefix = os.getenv("COS_AUDIT_PREFIX", "audit/")

    # Garante que o titular está no cofre (pode já estar de outro teste)
    pseudonym_id = real_vault.store(test_cpf)
    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")
    cleanup.add_doc(vault_db, pseudonym_id)
    cleanup.add_pseudonym(pseudonym_id)

    trace_id = real_audit.record_event(
        "agent1.snapshot_persisted",
        pseudonym_id=pseudonym_id,
        actor="integration_test",
        snapshot_id="x",
        test_fixture=True,
    )

    cleanup.add_doc(audit_db, trace_id)

    # Verificar documento no Cloudant
    doc = cloudant_client.get_document(db=audit_db, doc_id=trace_id).get_result()
    assert doc.get("trace_id") == trace_id
    doc_json = json.dumps(doc)
    from src.privacy.pseudonymization import normalize_cpf
    cpf_normalized = normalize_cpf(test_cpf)
    assert cpf_normalized not in doc_json, "CPF encontrado no documento de auditoria"

    # Verificar objeto no COS (se credenciais disponíveis)
    access_key = os.getenv("COS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("COS_SECRET_ACCESS_KEY", "")
    bucket = os.getenv("COS_BUCKET_NAME", "")

    if not access_key or not secret_key or not bucket:
        pytest.skip("credenciais COS ausentes — parte COS ignorada")

    cos_key = _cos_key_for(trace_id, doc.get("recorded_at", ""), cos_prefix)
    cleanup.add_cos(cos_key)

    import ibm_boto3
    from ibm_botocore.client import Config

    cos_client = ibm_boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        endpoint_url="https://s3.us-south.cloud-object-storage.appdomain.cloud",
        region_name="us-south",
    )

    # head_object verifica existência
    cos_client.head_object(Bucket=bucket, Key=cos_key)

    # Conteúdo JSON tem o trace_id e não contém o CPF
    obj = cos_client.get_object(Bucket=bucket, Key=cos_key)
    cos_body = obj["Body"].read().decode("utf-8")
    cos_record = json.loads(cos_body)
    assert cos_record.get("trace_id") == trace_id
    assert cpf_normalized not in cos_body, "CPF encontrado no objeto COS"


# ---------------------------------------------------------------------------
# d. test_debts_end_to_end
# ---------------------------------------------------------------------------

def test_debts_end_to_end(real_env, test_cpf, real_vault, real_audit, cloudant_client, cleanup):
    """
    Cria consentimento AUTHORISED → GET /debts (Granite real) → 200.
    Snapshot salvo tem key_version; não contém PII; existe evento audit.
    """
    import os
    from ibmcloudant.cloudant_v1 import Document

    # Pular se watsonx não configurado
    watsonx_url = os.getenv("WATSONX_URL", "")
    watsonx_project = os.getenv("WATSONX_PROJECT_ID", "")
    watsonx_model = os.getenv("WATSONX_MODEL_ID", "")
    ibm_api_key = os.getenv("IBM_CLOUD_API_KEY", "")
    if not watsonx_url or not watsonx_project or not ibm_api_key:
        pytest.skip("credenciais watsonx ausentes — teste de ponta a ponta ignorado")

    from src.privacy.pseudonymization import normalize_cpf
    from src.privacy.pii_guard import assert_no_pii

    consents_db = os.getenv("CLOUDANT_DB_CONSENTS", "consent_registry")
    snapshots_db = os.getenv("CLOUDANT_DB_SNAPSHOTS", "financial_snapshots")
    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")

    # Gerar pseudonym_id via cofre
    pseudonym_id = real_vault.store(test_cpf)
    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")
    cleanup.add_doc(vault_db, pseudonym_id)
    cleanup.add_pseudonym(pseudonym_id)

    # Criar consentimento AUTHORISED de teste
    consent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    consent_doc = {
        "_id": consent_id,
        "consent_id": consent_id,
        "pseudonym_id": pseudonym_id,
        "institutions": ["bb", "itau", "nubank"],
        "scopes": ["debts:read", "simulation:read", "recommendation:read"],
        "status": "AUTHORISED",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "test_fixture": True,
    }
    cloudant_client.put_document(
        db=consents_db,
        doc_id=consent_id,
        document=Document.from_dict(consent_doc),
    )
    cleanup.add_doc(consents_db, consent_id)

    # Chamar GET /debts via TestClient
    from src.main import app

    # Timeout generoso: Granite pode levar ~60s
    with TestClient(app, raise_server_exceptions=False) as tc:
        resp = tc.get(
            "/debts",
            params={"consent_id": consent_id},
        )

    assert resp.status_code == 200, f"GET /debts retornou {resp.status_code}: {resp.text}"

    # Buscar snapshot salvo
    result = cloudant_client.post_find(
        db=snapshots_db,
        selector={"pseudonym_id": pseudonym_id, "consent_id": consent_id},
        limit=1,
    ).get_result()
    docs = result.get("docs", [])
    assert docs, "snapshot não foi salvo no Cloudant"
    snapshot = docs[0]
    cleanup.add_doc(snapshots_db, snapshot["_id"])

    # Snapshot deve ter key_version
    assert "key_version" in snapshot, "snapshot sem campo key_version"

    # assert_no_pii passa (nenhuma PII em claro)
    assert_no_pii(snapshot)

    # CPF não aparece no JSON serializado
    cpf_normalized = normalize_cpf(test_cpf)
    assert cpf_normalized not in json.dumps(snapshot)

    # Evento agent1.snapshot_persisted no audit_trail
    snapshot_id = snapshot.get("snapshot_id") or snapshot.get("_id")
    audit_result = cloudant_client.post_find(
        db=audit_db,
        selector={"pseudonym_id": pseudonym_id, "event_type": "agent1.snapshot_persisted"},
        limit=10,
    ).get_result()
    audit_docs = audit_result.get("docs", [])
    assert audit_docs, "evento agent1.snapshot_persisted não encontrado no audit_trail"

    # Registrar trace_ids de auditoria para limpeza
    for adoc in audit_docs:
        cleanup.add_doc(audit_db, adoc["_id"])


# ---------------------------------------------------------------------------
# e. test_internal_erase_real
# ---------------------------------------------------------------------------

def test_internal_erase_real(real_env, real_vault, real_audit, cloudant_client, cleanup):
    """
    Cria consentimento + snapshot de teste → POST /internal/erase → 200,
    contagens corretas, snapshot sumiu, reveal falha. Segunda chamada → zeros.
    """
    import os
    from ibmcloudant.cloudant_v1 import Document
    from src.privacy.identity_vault import VaultError

    consents_db = os.getenv("CLOUDANT_DB_CONSENTS", "consent_registry")
    snapshots_db = os.getenv("CLOUDANT_DB_SNAPSHOTS", "financial_snapshots")
    audit_db = os.getenv("CLOUDANT_DB_AUDIT", "audit_trail")
    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")
    internal_token = os.getenv("INTERNAL_API_TOKEN", "test-internal-token-do-not-use-in-production")

    # CPF próprio para este teste — evita contar consentimentos de outros testes
    erase_cpf = _gen_random_cpf()
    pseudonym_id = real_vault.store(erase_cpf)
    cleanup.add_doc(vault_db, pseudonym_id)
    cleanup.add_pseudonym(pseudonym_id)

    # Criar consentimento de teste
    consent_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    consent_doc = {
        "_id": consent_id,
        "consent_id": consent_id,
        "pseudonym_id": pseudonym_id,
        "institutions": ["bb"],
        "scopes": ["debts:read"],
        "status": "AUTHORISED",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "test_fixture": True,
    }
    cloudant_client.put_document(
        db=consents_db,
        doc_id=consent_id,
        document=Document.from_dict(consent_doc),
    )
    cleanup.add_doc(consents_db, consent_id)

    # Criar snapshot de teste
    snapshot_id = str(uuid.uuid4())
    snapshot_doc = {
        "_id": snapshot_id,
        "snapshot_id": snapshot_id,
        "pseudonym_id": pseudonym_id,
        "consent_id": consent_id,
        "expires_at": (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "test_fixture": True,
    }
    cloudant_client.put_document(
        db=snapshots_db,
        doc_id=snapshot_id,
        document=Document.from_dict(snapshot_doc),
    )
    cleanup.add_doc(snapshots_db, snapshot_id)

    # Chamar POST /internal/erase via TestClient
    from src.main import app
    with TestClient(app) as tc:
        resp = tc.post(
            f"/internal/erase/{pseudonym_id}",
            headers={"X-Internal-Token": internal_token},
        )

    assert resp.status_code == 200, f"erase retornou {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["consents_revoked"] == 1
    assert body["snapshots_deleted"] == 1
    assert body["vault_shredded"] is True
    assert "trace_id" in body

    # Registrar trace_ids de auditoria para limpeza
    trace_id = body["trace_id"]
    cleanup.add_doc(audit_db, trace_id)

    # Snapshot deve ter sumido
    from ibm_cloud_sdk_core import ApiException
    try:
        cloudant_client.get_document(db=snapshots_db, doc_id=snapshot_id).get_result()
        assert False, "snapshot ainda existe após erase"
    except ApiException as exc:
        assert exc.status_code == 404

    # reveal deve falhar (shredded)
    with pytest.raises(VaultError):
        real_vault.reveal(pseudonym_id, purpose="LEGAL_REQUEST", actor="integration_test")

    # Segunda chamada → zeros
    from src.privacy.retention_store import get_retention_store
    from src.privacy.audit import get_audit_logger

    get_retention_store.cache_clear()
    get_audit_logger.cache_clear()

    with TestClient(app) as tc2:
        resp2 = tc2.post(
            f"/internal/erase/{pseudonym_id}",
            headers={"X-Internal-Token": internal_token},
        )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["consents_revoked"] == 0
    assert body2["snapshots_deleted"] == 0
    assert body2["vault_shredded"] is False

    # Registrar trace_id da segunda chamada
    cleanup.add_doc(audit_db, body2["trace_id"])


# ---------------------------------------------------------------------------
# f. test_retention_dry_run_real
# ---------------------------------------------------------------------------

def test_retention_dry_run_real(real_env, test_cpf, real_vault, real_audit, cloudant_client, cleanup):
    """
    Cria consentimento AWAITING expirado e snapshot expirado (test_fixture) →
    run_retention(dry_run=True) → contagens >= 1; documentos de teste CONTINUAM.
    """
    import os
    from ibmcloudant.cloudant_v1 import Document
    from src.privacy.retention import run_retention

    consents_db = os.getenv("CLOUDANT_DB_CONSENTS", "consent_registry")
    snapshots_db = os.getenv("CLOUDANT_DB_SNAPSHOTS", "financial_snapshots")
    vault_db = os.getenv("CLOUDANT_DB_IDENTITY_VAULT", "identity_vault")

    # Novo pseudonym_id para este teste (CPF já usado pode estar shredded)
    cpf2 = _gen_random_cpf()
    pseudonym_id2 = real_vault.store(cpf2)
    cleanup.add_doc(vault_db, pseudonym_id2)
    cleanup.add_pseudonym(pseudonym_id2)

    # Consentimento AWAITING com awaiting_expires_at no passado
    now = datetime.now(timezone.utc)
    consent_awaiting_id = str(uuid.uuid4())
    consent_awaiting = {
        "_id": consent_awaiting_id,
        "consent_id": consent_awaiting_id,
        "pseudonym_id": pseudonym_id2,
        "status": "AWAITING_AUTHORISATION",
        "awaiting_expires_at": _past_iso(2),
        "created_at": _past_iso(3),
        "test_fixture": True,
    }
    cloudant_client.put_document(
        db=consents_db,
        doc_id=consent_awaiting_id,
        document=Document.from_dict(consent_awaiting),
    )
    cleanup.add_doc(consents_db, consent_awaiting_id)

    # Snapshot expirado
    snapshot_exp_id = str(uuid.uuid4())
    snapshot_exp = {
        "_id": snapshot_exp_id,
        "snapshot_id": snapshot_exp_id,
        "pseudonym_id": pseudonym_id2,
        "expires_at": _past_iso(1),
        "test_fixture": True,
    }
    cloudant_client.put_document(
        db=snapshots_db,
        doc_id=snapshot_exp_id,
        document=Document.from_dict(snapshot_exp),
    )
    cleanup.add_doc(snapshots_db, snapshot_exp_id)

    # Coletor em memória para auditoria (não gravar no Cloudant real)
    events_collected: list[dict] = []

    def _audit_collector(event_type: str, *, pseudonym_id, actor, **fields) -> str:
        tid = str(uuid.uuid4())
        events_collected.append({"event_type": event_type, "trace_id": tid, **fields})
        return tid

    # run_retention em dry_run=True
    from src.privacy.retention_store import get_retention_store
    store = get_retention_store()

    report = run_retention(
        dry_run=True,
        store=store,
        vault=real_vault,
        audit=_audit_collector,
    )

    # Contagens devem ser >= 1 (pode haver outros docs na base real)
    assert report.consents_awaiting_deleted >= 1, (
        f"consents_awaiting_deleted={report.consents_awaiting_deleted}, esperado >= 1"
    )
    assert report.snapshots_deleted >= 1, (
        f"snapshots_deleted={report.snapshots_deleted}, esperado >= 1"
    )

    # Documentos de teste devem CONTINUAR existindo (dry_run não apaga)
    doc_await = cloudant_client.get_document(
        db=consents_db, doc_id=consent_awaiting_id
    ).get_result()
    assert doc_await.get("_id") == consent_awaiting_id, (
        "consentimento AWAITING foi apagado em dry_run — não devia"
    )

    doc_snap = cloudant_client.get_document(
        db=snapshots_db, doc_id=snapshot_exp_id
    ).get_result()
    assert doc_snap.get("_id") == snapshot_exp_id, (
        "snapshot expirado foi apagado em dry_run — não devia"
    )
