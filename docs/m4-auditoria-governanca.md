# M4 — Auditoria e Governança

Este documento descreve a arquitetura de trilha de auditoria, os destinos de gravação, a estrutura dos eventos, o plano de governança com watsonx.governance (AI Factsheets) e os alertas de acesso anômalo planejados para o Activity Tracker. Distingue o que está **Implementado (Parte A)** do que é **Plano / produção**. A fonte da verdade é o código.

---

## 1. Visão geral da trilha de auditoria

```
record_event(event_type, *, pseudonym_id, actor, **fields)
        │
        ├─ 1. Validação: event_type na lista fechada
        ├─ 2. Guarda de PII (assert_no_pii) — se violar → audit.pii_blocked
        ├─ 3. Cloudant (audit_trail)        ← sempre em produção
        ├─ 4. COS (audit/{AAAA}/{MM}/{DD}/{trace_id}.json)  ← se credenciais presentes
        └─ 5. GovernanceSink                ← NoopGovernanceSink (Parte A);
                                              WatsonxGovernanceSink (Plano)
```

Falhas em qualquer destino (3, 4 ou 5) são registradas como `logger.error` estruturado **sem PII** e **nunca propagam** para o fluxo chamador. `record_event` sempre retorna um `trace_id` (UUID4).

---

## 2. Tipos de evento (lista fechada)

Definidos em [`src/privacy/audit.py`](../src/privacy/audit.py), constante `_ALLOWED_EVENT_TYPES`. Qualquer `event_type` fora desta lista levanta `ValueError` imediatamente.

| Evento | Quando ocorre | Actor típico |
|---|---|---|
| `consent.expired` | Consentimento `AUTHORISED` com `expires_at` expirado detectado pelo job de retenção | `"retention"` |
| `consent.erasure_requested` | Início do endpoint `POST /internal/erase/{pseudonym_id}` | `"internal_api"` |
| `erasure.completed` | Conclusão bem-sucedida do apagamento (contagens incluídas) | `"internal_api"` |
| `vault.reveal` | Toda chamada a `IdentityVault.reveal()`, com sucesso (`ok=true`) ou falha (`ok=false`) | `"agent1"` (Serasa) ou outro |
| `retention.run` | Conclusão do job de retenção (inclusive `dry_run=true`) | `"retention"` |
| `agent1.snapshot_persisted` | Snapshot gravado com sucesso em `persist_snapshot()` | `"agent1"` |
| `audit.pii_blocked` | PII detectado em campos de outro evento — o evento original é descartado e substituído por este | `"agent1"` / original |

---

## 3. Estrutura de um documento de auditoria

```json
{
  "_id":          "<trace_id>",
  "type":         "audit_event",
  "trace_id":     "<UUID4>",
  "recorded_at":  "2026-11-05T18:00:00Z",
  "event_type":   "agent1.snapshot_persisted",
  "pseudonym_id": "<hex-64-chars>",
  "actor":        "agent1",
  "snapshot_id":  "<uuid>",
  "consent_id":   "<uuid>",
  "model_id":     "ibm/granite-4-h-small",
  "tool_calls":   ["get_open_finance_debts", "get_serasa_data", "calculate_cet", "persist_snapshot"]
}
```

> **Nunca** haverá CPF, nome, número de contrato ou score exato em nenhum campo. A guarda de PII (`assert_no_pii`) é aplicada antes de qualquer gravação.

---

## 4. Campos adicionais por tipo de evento

| Evento | Campos extras obrigatórios |
|---|---|
| `consent.expired` | `consent_id` |
| `consent.erasure_requested` | _(apenas os campos base)_ |
| `erasure.completed` | `consents_revoked`, `snapshots_deleted`, `vault_shredded` (bool), `request_trace_id` |
| `vault.reveal` | `purpose` (`SERASA_QUERY` ou `LEGAL_REQUEST`), `ok` (bool), `reason` (apenas em falha) |
| `retention.run` | `snapshots_deleted`, `consents_awaiting_deleted`, `consents_expired`, `consents_revoked_deleted`, `vault_shredded`, `skipped_invalid`, `dry_run` |
| `agent1.snapshot_persisted` | `snapshot_id`, `consent_id`, `model_id`, `tool_calls` (lista de nomes) |
| `audit.pii_blocked` | `blocked_event_type`, `violation_path` |

---

## 5. Destinos de gravação

### 5.1 Cloudant — `audit_trail` (Implementado)

- Banco: `audit_trail` (configurado em `CLOUDANT_DB_AUDIT`; padrão: `"audit_trail"`).
- Classe: `CloudantAuditRepository` em [`src/privacy/audit.py`](../src/privacy/audit.py).
- Autenticação: IAM via `IAMAuthenticator` (SDK `ibmcloudant`).
- `_id = trace_id` — identificador único do evento.
- **Nunca expurgado** pelo job de retenção.

### 5.2 IBM Cloud Object Storage — COS (Implementado)

- Bucket: `COS_BUCKET_NAME` (ex.: `desendividai-artifacts`).
- Prefixo: `COS_AUDIT_PREFIX` (padrão: `audit/`).
- Chave do objeto: `audit/{AAAA}/{MM}/{DD}/{trace_id}.json`.
- Endpoint: `https://s3.us-south.cloud-object-storage.appdomain.cloud`.
- Credenciais: HMAC (`COS_ACCESS_KEY_ID`, `COS_SECRET_ACCESS_KEY`).
- Classe: `CosAuditSink` em [`src/privacy/audit.py`](../src/privacy/audit.py).
- **Sem credenciais** (testes, dev): `get_cos_sink()` retorna `None` silenciosamente; apenas o Cloudant é usado.
- O client `ibm_boto3` é **injetável** nos testes (sem rede real).

### 5.3 GovernanceSink — Noop / watsonx.governance

| Implementação | Estado | Descrição |
|---|---|---|
| `NoopGovernanceSink` | **Ativo (Parte A)** | Descarta silenciosamente todos os registros |
| `WatsonxGovernanceSink` | **Plano / produção** | Esqueleto — ver seção 6 |

---

## 6. Plano — watsonx.governance e AI Factsheets (Parte B)

> Este bloco é **plano / produção**: a integração não está implementada. `WatsonxGovernanceSink` é um esqueleto em [`src/privacy/audit.py`](../src/privacy/audit.py) que levanta `NotImplementedError`.

**Objetivo:** registrar cada sessão do Agente 1 como um evento de uso do modelo no AI Factsheets, permitindo rastreabilidade fim a fim da cadeia decisória.

**Configuração prevista:**

| Variável | Descrição |
|---|---|
| `WATSONX_GOVERNANCE_URL` | URL da instância watsonx.governance |
| `WATSONX_PROJECT_ID` | ID do projeto associado ao Agente 1 |

**Fluxo previsto (Parte B):**

1. `WatsonxGovernanceSink.send(record)` recebe o registro de auditoria já sanitizado (sem PII).
2. O `trace_id` é usado como **chave de correlação** entre o evento de auditoria e a entrada no AI Factsheets.
3. O campo `tool_calls` do evento `agent1.snapshot_persisted` alimenta o rastreamento de quais ferramentas o modelo invocou.
4. Autenticação via `IAMAuthenticator` com `IBM_CLOUD_API_KEY`.

---

## 7. Plano — Alertas de acesso anômalo (Activity Tracker)

> Este bloco é **plano / produção**. O Activity Tracker da IBM Cloud pode ser configurado para detectar padrões suspeitos nos eventos do Cloudant e COS.

### Alertas recomendados

| Alerta | Gatilho | Severidade | Ação sugerida |
|---|---|---|---|
| Leituras em massa no `identity_vault` | > N leituras do documento `identity_vault` em intervalo < T (ex.: 100 leituras em 5 min) | CRÍTICO | Notificar DPO; bloquear temporariamente o actor |
| `vault.reveal` fora do padrão | `purpose` fora de `SERASA_QUERY` / `LEGAL_REQUEST`; ou `ok=false` repetido > M vezes | ALTO | Investigar comprometimento; revisar logs |
| Falhas de autenticação no endpoint `/internal/*` | > K tentativas com `X-Internal-Token` inválido em T min | ALTO | Bloquear IP; notificar equipe de segurança |
| `audit.pii_blocked` recorrente | Mesmo `actor` gerando > P bloqueios de PII em T min | MÉDIO | Investigar bug ou ataque de injeção |
| Acesso à KEK fora do horário operacional | Chamadas ao Secrets Manager (produção) fora do horário comercial | MÉDIO | Notificar DPO e equipe de infra |

> Os limiares (N, T, M, K, P) devem ser calibrados pela equipe de segurança com base no volume real de operações.

### Configuração no Activity Tracker (produção)

1. Habilitar **IBM Cloud Activity Tracker** na região `us-south`.
2. Criar **alertas de threshold** sobre os eventos do Cloudant (`database.read`, `database.write`) filtrando pelo banco `identity_vault`.
3. Integrar com **IBM Cloud Monitoring** ou **PagerDuty** para notificações em tempo real.
4. Os eventos de `vault.reveal` já chegam ao `audit_trail` com `ok=true/false` — exportar para o Activity Tracker via streaming de logs do COS.

---

## 8. Conformidade — o que o `audit_trail` nunca deve conter

A guarda de PII ([`src/privacy/pii_guard.py`](../src/privacy/pii_guard.py)) é aplicada antes de gravar qualquer evento. Se detectar PII:

1. O evento original é descartado.
2. Um evento substituto `audit.pii_blocked` é gravado com `blocked_event_type` e `violation_path`.
3. O `trace_id` do evento substituto é retornado.

Isso garante que nenhum CPF, número de contrato ou outro dado pessoal chegue ao Cloudant, ao COS ou ao GovernanceSink.

---

## Referências no código

| Módulo | Caminho |
|---|---|
| AuditLogger, eventos, destinos | [`src/privacy/audit.py`](../src/privacy/audit.py) |
| Guarda de PII | [`src/privacy/pii_guard.py`](../src/privacy/pii_guard.py) |
| Integração no persist_snapshot | [`src/agents/agent1/tools.py`](../src/agents/agent1/tools.py) |
| Integração no cofre (vault.reveal) | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Evento retention.run | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Eventos erasure | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
