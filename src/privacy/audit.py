"""
Trilha de auditoria — M4, Fase 5.

Destinos:
  1. Cloudant (audit_trail) — sempre presente em produção.
  2. COS — opcional; ativo quando COS_ACCESS_KEY_ID, COS_SECRET_ACCESS_KEY e
     COS_BUCKET_NAME estiverem configurados.
  3. GovernanceSink — NoopGovernanceSink por padrão. Em produção, implementar
     um GovernanceSink para o watsonx.governance (AI Factsheets).

Todos os registros passam pela guarda de PII (assert_no_pii) antes de serem
gravados. Se a guarda falhar, um evento substituto `audit.pii_blocked` é
gravado em seu lugar — o registro original nunca chega a nenhum destino.

Falhas de gravação (Cloudant, COS, governance) são registradas como
logger.error estruturado (event_type, trace_id, destino, tipo da exceção).

Todo evento (já sanitizado) também sai como uma linha JSON no stdout pelo
logger `desendividai.audit` (ver _emit_log_line). É o sinal para alertas de
acesso anômalo sobre `event_type` em um pipeline de logs (IBM Cloud Logs em
produção). Esse log NÃO conta como destino persistido.

Se pelo menos um destino persistir com sucesso, record_event retorna o trace_id.
Se NENHUM destino persistir (Cloudant e COS ambos falharam), _dispatch levanta
AuditWriteError. O fluxo chamador é responsável por tratar esse erro conforme
sua política (ver erasure.py, retention.py).

Garantias de integridade da trilha:
  A trilha é append-only pela aplicação (o código só insere; não há update/delete).
  Para proteção adicional em produção:
    - COS com Object Lock/retention (WORM) para as cópias JSON.
    - Hash chain entre eventos (a implementar em produção).
    - Credencial exclusiva de escrita separada da CLOUDANT_APIKEY usada para
      apagar snapshots (não usar a mesma chave IAM para auditoria e expurgo).
  Sem essas medidas de produção, a trilha pode ser alterada por quem tiver acesso
  direto ao Cloudant ou COS.

Compatibilidade com IdentityVault:
  vault = IdentityVault(repo, audit=audit_logger.record_event)
  O vault chama audit("vault.reveal", pseudonym_id=..., actor=..., purpose=..., ok=...)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable, Optional, Protocol

from src.core.clock import utcnow_iso as _utcnow_iso

from .pii_guard import PIIViolation, assert_no_pii


# ---------------------------------------------------------------------------
# Exceção de falha total de auditoria
# ---------------------------------------------------------------------------

class AuditWriteError(RuntimeError):
    """
    Levantada por _dispatch quando nenhum destino de auditoria (Cloudant, COS)
    persiste o registro. A mensagem não contém dados pessoais.
    """

logger = logging.getLogger(__name__)

# Logger dos eventos de auditoria em JSON (uma linha por evento, sem PII).
AUDIT_EVENT_LOGGER_NAME = "desendividai.audit"
_LOG_LINE_EXCLUDED_FIELDS: frozenset[str] = frozenset({"_id", "_rev", "type"})


def _get_event_logger() -> logging.Logger:
    """Logger `desendividai.audit` com um StreamHandler(stdout) próprio.

    propagate=False para a linha não sair duplicada pelo root logger. O
    handler é adicionado uma vez só, mesmo com reimportação do módulo.
    """
    import sys

    event_logger = logging.getLogger(AUDIT_EVENT_LOGGER_NAME)
    if not any(getattr(h, "_desendividai_audit", False) for h in event_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._desendividai_audit = True  # type: ignore[attr-defined]
        event_logger.addHandler(handler)
    event_logger.setLevel(logging.INFO)
    event_logger.propagate = False
    return event_logger


_event_logger = _get_event_logger()


def emit_security_event(event_type: str, **fields) -> None:
    """
    Emite um evento de segurança SÓ como linha JSON no stdout (não persiste).

    Para sinais de alto volume e sem titular, como falhas de autenticação no
    /internal, que não devem encher o audit_trail. Os campos passam pela guarda
    de PII; se violarem, a linha sai só com event_type e pii_blocked=true.
    """
    from src.core.clock import utcnow_iso

    line = {"event_type": event_type, "recorded_at": utcnow_iso(), "persisted": False, **fields}
    try:
        assert_no_pii(line)
    except PIIViolation:
        line = {"event_type": event_type, "recorded_at": line["recorded_at"], "pii_blocked": True}
    try:
        _event_logger.info(json.dumps(line, ensure_ascii=False, default=str, sort_keys=True))
    except Exception as exc:  # pragma: no cover - defensivo
        logger.error(f"audit: falha ao emitir evento de segurança exc={type(exc).__name__}")


def _emit_log_line(record: dict, persisted: int) -> None:
    """Emite o evento como JSON no stdout. Falhas nunca propagam."""
    try:
        line = {k: v for k, v in record.items() if k not in _LOG_LINE_EXCLUDED_FIELDS}
        line["audit_persisted"] = persisted
        _event_logger.info(
            json.dumps(line, ensure_ascii=False, default=str, sort_keys=True)
        )
    except Exception as exc:  # pragma: no cover - defensivo
        logger.error(
            f"audit: falha ao emitir log do evento exc={type(exc).__name__}",
            extra={"exc_type": type(exc).__name__},
        )

# ---------------------------------------------------------------------------
# Tipos de evento permitidos
# ---------------------------------------------------------------------------

_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "consent.created",
    "consent.authorised",
    "consent.rejected",
    "consent.revoked",
    "consent.expired",
    "consent.erasure_requested",
    "erasure.completed",
    "subject.access_exported",
    "vault.reveal",
    "retention.run",
    "agent1.snapshot_persisted",
    "audit.pii_blocked",
    "agent2.simulation_created",
    "agent2.recommendation_generated",
})

_RESERVED_FIELDS: frozenset[str] = frozenset({
    "_id", "_rev", "type", "trace_id", "recorded_at",
    "event_type", "pseudonym_id", "actor",
})


# ---------------------------------------------------------------------------
# Repositório de auditoria
# ---------------------------------------------------------------------------

class AuditRepository(Protocol):
    def put(self, doc: dict) -> None: ...
    def find(self, selector: dict, limit: int = 25) -> list[dict]: ...


class InMemoryAuditRepository:
    """Repositório em memória — apenas para testes (sem rede)."""

    def __init__(self) -> None:
        self._store: list[dict] = {}  # type: ignore[assignment]
        self._store = []

    def put(self, doc: dict) -> None:
        # Simulação de inserção simples: substitui documento com o mesmo _id em memória.
        # No Cloudant real, put_document sem _rev num _id já existente devolve 409 Conflict.
        self._store = [d for d in self._store if d.get("_id") != doc.get("_id")]
        self._store.append(dict(doc))

    def find(self, selector: dict, limit: int = 25) -> list[dict]:
        results = []
        for doc in self._store:
            if all(doc.get(k) == v for k, v in selector.items()):
                results.append(dict(doc))
                if len(results) >= limit:
                    break
        return results


class CloudantAuditRepository:
    """
    Repositório Cloudant para o audit_trail.

    Usa o SDK ibmcloudant síncrono + IAMAuthenticator.
    Banco configurado em CLOUDANT_DB_AUDIT (padrão "audit_trail").
    _id = trace_id.
    put() insere o documento via put_document.
    find() usa post_find (Mango).
    """

    def __init__(self, client, db: str = "audit_trail") -> None:
        self._client = client
        self._db = db

    def put(self, doc: dict) -> None:
        from ibmcloudant.cloudant_v1 import Document

        self._client.put_document(
            db=self._db,
            doc_id=doc["_id"],
            document=Document.from_dict(doc),
        )

    _PAGE_SIZE = 200

    def find(self, selector: dict, limit: int = 25) -> list[dict]:
        """post_find paginado por bookmark até `limit` documentos."""
        docs: list[dict] = []
        bookmark: Optional[str] = None
        while len(docs) < limit:
            page_size = min(self._PAGE_SIZE, limit - len(docs))
            kwargs: dict = {"db": self._db, "selector": selector, "limit": page_size}
            if bookmark:
                kwargs["bookmark"] = bookmark
            result = self._client.post_find(**kwargs).get_result()
            page = result.get("docs", [])
            docs.extend(page)
            next_bookmark = result.get("bookmark")
            if len(page) < page_size or not next_bookmark or next_bookmark == bookmark:
                break
            bookmark = next_bookmark
        return docs[:limit]


@lru_cache(maxsize=1)
def get_audit_repository() -> AuditRepository:
    """
    Factory com @lru_cache — padrão do M2.
    Requer CLOUDANT_URL e CLOUDANT_APIKEY.
    """
    from src.core.cloudant import build_cloudant_client
    from src.core.config import ConfigurationError, audit_db

    try:
        client = build_cloudant_client()
    except ConfigurationError:
        raise ConfigurationError(
            "CLOUDANT_URL e CLOUDANT_APIKEY são obrigatórios para CloudantAuditRepository."
        )
    return CloudantAuditRepository(client=client, db=audit_db())


# ---------------------------------------------------------------------------
# Sink COS
# ---------------------------------------------------------------------------

class CosAuditSink:
    """
    Grava cópias JSON dos registros de auditoria no IBM Cloud Object Storage.

    Credenciais HMAC: COS_ACCESS_KEY_ID, COS_SECRET_ACCESS_KEY.
    Bucket: COS_BUCKET_NAME.
    Prefixo: COS_AUDIT_PREFIX (padrão "audit/").
    Endpoint: https://s3.us-south.cloud-object-storage.appdomain.cloud
    Chave do objeto: {prefix}{AAAA}/{MM}/{DD}/{trace_id}.json
    (data extraída de recorded_at em UTC)

    O client ibm_boto3 é injetável para testes (fake em memória).
    """

    _ENDPOINT = "https://s3.us-south.cloud-object-storage.appdomain.cloud"

    def __init__(
        self,
        bucket: str,
        prefix: str = "audit/",
        client=None,  # injetável; se None, cria ibm_boto3 real
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix
        if client is not None:
            self._client = client
        else:
            import ibm_boto3
            from ibm_botocore.client import Config

            self._client = ibm_boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(signature_version="s3v4"),
                endpoint_url=self._ENDPOINT,
                region_name="us-south",
            )

    def send(self, record: dict) -> None:
        """Grava record como JSON na chave {prefix}AAAA/MM/DD/{trace_id}.json."""
        recorded_at: str = record.get("recorded_at", "")
        # Parse date from ISO-8601Z string
        try:
            dt = datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        trace_id: str = record.get("trace_id", str(uuid.uuid4()))
        key = (
            f"{self._prefix}"
            f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}"
            f"/{trace_id}.json"
        )
        body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )


def get_cos_sink() -> Optional[CosAuditSink]:
    """
    Factory para CosAuditSink.

    Retorna None (sem erro) se faltar qualquer credencial ou o bucket.
    Destinado a dev/testes onde COS não está configurado.
    """
    access_key = os.getenv("COS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("COS_SECRET_ACCESS_KEY", "")
    bucket = os.getenv("COS_BUCKET_NAME", "")
    if not access_key or not secret_key or not bucket:
        return None
    prefix = os.getenv("COS_AUDIT_PREFIX", "audit/")
    return CosAuditSink(
        bucket=bucket,
        prefix=prefix,
        access_key=access_key,
        secret_key=secret_key,
    )


# ---------------------------------------------------------------------------
# GovernanceSink
# ---------------------------------------------------------------------------

class GovernanceSink(Protocol):
    def send(self, record: dict) -> None: ...


class NoopGovernanceSink:
    """Sink padrão: descarta silenciosamente todos os registros."""

    def send(self, record: dict) -> None:
        pass


# ---------------------------------------------------------------------------
# AuditLogger principal
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    Registra eventos de auditoria nos destinos configurados.

    Parâmetros:
        repository   — AuditRepository (Cloudant ou InMemory).
        cos_sink     — CosAuditSink opcional; None desativa o COS.
        governance   — GovernanceSink (padrão: NoopGovernanceSink).
        clock        — callable() -> str ISO-8601Z; injetável em testes.
    """

    def __init__(
        self,
        repository: AuditRepository,
        cos_sink: Optional[CosAuditSink] = None,
        governance: GovernanceSink = None,  # type: ignore[assignment]
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self._repo = repository
        self._cos = cos_sink
        self._gov: GovernanceSink = governance if governance is not None else NoopGovernanceSink()
        self._clock: Callable[[], str] = clock if clock is not None else _utcnow_iso

    def record_event(
        self,
        event_type: str,
        *,
        pseudonym_id: Optional[str],
        actor: str,
        **fields,
    ) -> str:
        """
        Grava um evento de auditoria e retorna o trace_id (UUID4 str).

        event_type deve ser um dos valores em _ALLOWED_EVENT_TYPES;
        caso contrário, ValueError é levantado imediatamente (erro de programação).

        Se **fields contiver chaves reservadas (_id, _rev, type, trace_id,
        recorded_at, event_type, pseudonym_id, actor), ValueError é levantado.

        O registro passa pela guarda de PII (assert_no_pii). Se violar:
          - O registro original NÃO é gravado em nenhum destino.
          - Um evento substituto `audit.pii_blocked` é gravado com
            {"blocked_event_type", "violation_path"}.
          - Se a violação era em pseudonym_id ou actor, esses campos são
            substituídos por None / "redacted" antes de gravar o substituto.
          - O trace_id desse evento substituto é retornado.

        Ordem de gravação: Cloudant → COS → governance.
        Falha em qualquer destino: logger.error estruturado (sem PII) + continua.
        """
        if event_type not in _ALLOWED_EVENT_TYPES:
            raise ValueError(
                f"event_type inválido: {event_type!r}. "
                f"Valores aceitos: {sorted(_ALLOWED_EVENT_TYPES)}"
            )

        reserved_used = _RESERVED_FIELDS & fields.keys()
        if reserved_used:
            raise ValueError(
                f"Campos reservados não podem ser passados em **fields: "
                f"{sorted(reserved_used)}"
            )

        trace_id = str(uuid.uuid4())
        record: dict = {
            "_id": trace_id,
            "type": "audit_event",
            "trace_id": trace_id,
            "recorded_at": self._clock(),
            "event_type": event_type,
            "pseudonym_id": pseudonym_id,
            "actor": actor,
            **fields,
        }

        # PII guard
        try:
            assert_no_pii(record)
        except PIIViolation as pii_exc:
            logger.error(
                "audit.pii_blocked: PII detectado antes da gravação",
                extra={
                    "event_type": event_type,
                    "violation_path": pii_exc.path,
                },
            )
            blocked_trace_id = str(uuid.uuid4())
            # Start with safe values; sanitize pseudonym_id/actor if they
            # were the source of the PII violation.
            safe_pseudonym_id: Optional[str] = pseudonym_id
            safe_actor: str = actor
            blocked_record: dict = {
                "_id": blocked_trace_id,
                "type": "audit_event",
                "trace_id": blocked_trace_id,
                "recorded_at": self._clock(),
                "event_type": "audit.pii_blocked",
                "pseudonym_id": safe_pseudonym_id,
                "actor": safe_actor,
                "blocked_event_type": event_type,
                "violation_path": pii_exc.path,
            }
            # Iteratively sanitize pseudonym_id / actor until blocked_record
            # passes the PII guard.
            while True:
                try:
                    assert_no_pii(blocked_record)
                    break
                except PIIViolation as inner_exc:
                    if inner_exc.path == "pseudonym_id":
                        blocked_record["pseudonym_id"] = None
                    elif inner_exc.path == "actor":
                        blocked_record["actor"] = "redacted"
                    else:
                        # Unexpected path in blocked_record — remove the field
                        blocked_record.pop(inner_exc.path, None)
            self._dispatch(blocked_record)
            return blocked_trace_id

        self._dispatch(record)
        return trace_id

    def find_events(self, selector: dict, limit: int = 1000) -> list[dict]:
        """Consulta a trilha (Cloudant) — usado na exportação dos dados do titular."""
        return self._repo.find(selector, limit=limit)

    def _dispatch(self, record: dict) -> None:
        """
        Envia o registro para todos os destinos; erros são logados.

        Se NENHUM destino de persistência (Cloudant e COS) gravar com sucesso,
        levanta AuditWriteError (sem dados pessoais na mensagem).
        Governance não conta como destino de persistência para este critério.
        """
        trace_id = record.get("trace_id", "")
        event_type = record.get("event_type", "")

        persisted = 0

        # 1. Cloudant
        try:
            self._repo.put(record)
            persisted += 1
        except Exception as exc:
            exc_type = type(exc).__name__
            logger.error(
                f"audit: falha ao gravar destino=cloudant trace_id={trace_id} "
                f"event_type={event_type} exc={exc_type}",
                extra={
                    "event_type": event_type,
                    "trace_id": trace_id,
                    "destino": "cloudant",
                    "exc_type": exc_type,
                },
            )

        # 2. COS
        if self._cos is not None:
            try:
                self._cos.send(record)
                persisted += 1
            except Exception as exc:
                exc_type = type(exc).__name__
                logger.error(
                    f"audit: falha ao gravar destino=cos trace_id={trace_id} "
                    f"event_type={event_type} exc={exc_type}",
                    extra={
                        "event_type": event_type,
                        "trace_id": trace_id,
                        "destino": "cos",
                        "exc_type": exc_type,
                    },
                )

        # 3. Governance (não conta como destino de persistência)
        try:
            self._gov.send(record)
        except Exception as exc:
            exc_type = type(exc).__name__
            logger.error(
                f"audit: falha ao gravar destino=governance trace_id={trace_id} "
                f"event_type={event_type} exc={exc_type}",
                extra={
                    "event_type": event_type,
                    "trace_id": trace_id,
                    "destino": "governance",
                    "exc_type": exc_type,
                },
            )

        # 4. Log JSON no stdout (não conta como destino persistido)
        _emit_log_line(record, persisted)

        if persisted == 0:
            raise AuditWriteError(
                f"Nenhum destino de auditoria disponível "
                f"(event_type={event_type} trace_id={trace_id})"
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_audit_logger() -> AuditLogger:
    """
    Factory com @lru_cache — monta Cloudant + COS (se configurado) + Noop.
    """
    repo = get_audit_repository()
    cos = get_cos_sink()
    return AuditLogger(repository=repo, cos_sink=cos)


def get_audit_logger_or_none() -> Optional["AuditLogger"]:
    """
    Retorna o AuditLogger ou None se get_audit_logger() levantar qualquer
    exceção (ex.: ConfigurationError quando CLOUDANT_URL/APIKEY ausentes).
    Registra logger.warning apenas com o tipo da exceção (sem dados de configuração ou PII).
    """
    try:
        return get_audit_logger()
    except Exception as exc:
        logger.warning(
            "get_audit_logger() falhou; auditoria indisponível: %s",
            type(exc).__name__,
        )
        return None
