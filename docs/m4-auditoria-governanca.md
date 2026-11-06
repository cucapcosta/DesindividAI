# M4 — Auditoria e Governança

Este documento descreve a arquitetura de trilha de auditoria, os destinos de gravação, a estrutura dos eventos, a integração pendente com o watsonx.governance (AI Factsheets) e os alertas de acesso anômalo sobre os logs estruturados de auditoria. Distingue o que está **Implementado (Parte A e Parte B)** do que é **Plano / produção**. A fonte da verdade é o código.

---

## 1. Visão geral da trilha de auditoria

```
record_event(event_type, *, pseudonym_id, actor, **fields)
        │
        ├─ 1. Validação: event_type na lista fechada
        ├─ 2. Guarda de PII (assert_no_pii) — se violar → audit.pii_blocked
        ├─ 3. Cloudant (audit_trail)        ← sempre em produção
        ├─ 4. COS (audit/{AAAA}/{MM}/{DD}/{trace_id}.json)  ← se credenciais presentes
        ├─ 5. GovernanceSink                ← NoopGovernanceSink (ativo): hoje NADA
        │                                     é enviado ao watsonx.governance (pendência)
        └─ 6. Log JSON no stdout            ← logger `desendividai.audit`, uma linha por
                                              evento (não conta como destino persistido)
```

**Tratamento de falhas:**

- A falha de **um** destino (3, 4 ou 5) é registrada como `logger.error` estruturado **sem PII** (`event_type`, `trace_id`, `destino`, tipo da exceção) e não interrompe a gravação nos demais.
- Se **nenhum** destino de persistência gravar (Cloudant falhou e COS falhou ou não está configurado), `record_event` levanta `AuditWriteError`. GovernanceSink e o log no stdout não contam para esse critério.
- Quem depende desse erro: `POST /internal/erase`, `POST /internal/erase/{pseudonym_id}` e `POST /internal/subject/export` respondem `503 AUDIT_UNAVAILABLE` ([`src/api/routers/internal.py`](../src/api/routers/internal.py)), e [`src/privacy/erasure.py`](../src/privacy/erasure.py) não apaga nada se não conseguir auditar o pedido. Fluxos de melhor-esforço (Agente 2, consentimento do M1) chamam via [`audit_bridge.py`](../src/privacy/audit_bridge.py), que captura o erro e não derruba a requisição.
- Em caso de sucesso, `record_event` retorna o `trace_id` (UUID4) do evento gravado (ou do substituto `audit.pii_blocked`).

---

## 2. Tipos de evento (lista fechada)

Definidos em [`src/privacy/audit.py`](../src/privacy/audit.py), constante `_ALLOWED_EVENT_TYPES`. Qualquer `event_type` fora desta lista levanta `ValueError` imediatamente.

| Evento | Quando ocorre | Actor típico | Parte |
|---|---|---|---|
| `consent.created` | Consentimento criado em `POST /consent` (`AWAITING_AUTHORISATION`) | `"consent_api"` | M1 |
| `consent.authorised` | Callback de autorização aprovado pelo titular | `"consent_api"` | M1 |
| `consent.rejected` | Autorização negada, prazo de autorização expirado ou cancelamento antes de autorizar (`reason`) | `"consent_api"` | M1 |
| `consent.revoked` | Revogação pelo titular de um consentimento `AUTHORISED` | `"consent_api"` | M1 |
| `consent.expired` | Consentimento `AUTHORISED` com `expires_at` expirado detectado pelo job de retenção | `"retention"` | A |
| `consent.erasure_requested` | Início do endpoint `POST /internal/erase/{pseudonym_id}` | `"internal_api"` | A |
| `erasure.completed` | Conclusão bem-sucedida do apagamento (contagens incluídas, inclusive `decisions_deleted`) | `"internal_api"` | A+B |
| `vault.reveal` | Toda chamada a `IdentityVault.reveal()`, com sucesso (`ok=true`) ou falha (`ok=false`) — **inclui chamadas do Serasa real (Parte B)** | `"agent1"` (Serasa) ou outro | A+B |
| `retention.run` | Conclusão do job de retenção (inclusive `dry_run=true`) | `"retention"` | A |
| `agent1.snapshot_persisted` | Snapshot gravado com sucesso em `persist_snapshot()` | `"agent1"` | A |
| `audit.pii_blocked` | PII detectado em campos de outro evento — o evento original é descartado e substituído por este | `"agent1"` / original | A |
| `agent2.simulation_created` | Simulação gravada com sucesso pelo Agente 2 | `"agent2"` | B |
| `agent2.recommendation_generated` | Recomendação gerada e trace gravado pelo Agente 2 | `"agent2"` | B |
| `subject.access_exported` | Exportação dos dados do titular em `POST /internal/subject/export` (um evento por pseudônimo) | `"internal_api"` | Revisão M4 |

Fora da lista fechada, há um evento **só de log** (não vai para o `audit_trail`, ver §5.4): `internal.auth_failed`, emitido a cada 401 no `/internal/*`.

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
| `consent.created` | `consent_id`, `institutions`, `scopes`, `expires_at` |
| `consent.authorised` | `consent_id` |
| `consent.rejected` | `consent_id`, `reason` |
| `consent.revoked` | `consent_id`, `revocation_reason`, `revoked_by` |
| `consent.expired` | `consent_id` |
| `consent.erasure_requested` | _(apenas os campos base)_ |
| `erasure.completed` | `consents_revoked`, `snapshots_deleted`, `decisions_deleted`, `vault_shredded` (bool), `request_trace_id` |
| `vault.reveal` | `purpose` (`SERASA_QUERY` ou `LEGAL_REQUEST`), `ok` (bool), `reason` (apenas em falha: `invalid_purpose`, `actor_not_allowed`, `no_active_consent`) |
| `retention.run` | `snapshots_deleted`, `decisions_deleted`, `consents_awaiting_deleted`, `consents_rejected_deleted`, `consents_expired`, `consents_revoked_deleted`, `vault_shredded`, `audit_events_deleted`, `skipped_invalid`, `errors`, `dry_run`, `aborted` |
| `subject.access_exported` | `key_version`, `consents`, `snapshots`, `decisions`, `audit_events` (contagens) |
| `agent1.snapshot_persisted` | `snapshot_id`, `consent_id`, `model_id`, `tool_calls` (lista de nomes) |
| `audit.pii_blocked` | `blocked_event_type`, `violation_path` |
| `agent2.simulation_created` | `simulation_id`, `snapshot_id`, `consent_id` — nunca prompt ou output |
| `agent2.recommendation_generated` | `decision_trace_id`, `simulation_id`, `recommended_scenario`, `model_id`, `prompt_sha256`, `output_sha256` — **nunca** o texto do prompt ou da saída |

---

## 5. Destinos de gravação

### 5.1 Cloudant — `audit_trail` (Implementado)

- Banco: `audit_trail` (configurado em `CLOUDANT_DB_AUDIT`; padrão: `"audit_trail"`).
- Classe: `CloudantAuditRepository` em [`src/privacy/audit.py`](../src/privacy/audit.py).
- Autenticação: IAM via `IAMAuthenticator` (SDK `ibmcloudant`).
- `_id = trace_id` — identificador único do evento.
- **Não é expurgado por padrão.** Se `AUDIT_RETENTION_DAYS` for definido, a regra f do job de retenção apaga eventos mais antigos que o prazo (ver [`m4-retencao-e-delecao.md`](m4-retencao-e-delecao.md) §2). O prazo é decisão do controlador.
- A trilha é **append-only pela aplicação** (o código só insere; não há update). A única remoção é o expurgo por prazo da regra f.
  Requisito de produção: usar credencial exclusiva de escrita separada da `CLOUDANT_APIKEY`
  usada para apagar snapshots.

### 5.2 IBM Cloud Object Storage — COS (Implementado)

- Bucket: `COS_BUCKET_NAME` (ex.: `desendividai-artifacts`).
- Prefixo: `COS_AUDIT_PREFIX` (padrão: `audit/`).
- Chave do objeto: `audit/{AAAA}/{MM}/{DD}/{trace_id}.json`.
- Endpoint: `https://s3.us-south.cloud-object-storage.appdomain.cloud`.
- Credenciais: HMAC (`COS_ACCESS_KEY_ID`, `COS_SECRET_ACCESS_KEY`).
- Classe: `CosAuditSink` em [`src/privacy/audit.py`](../src/privacy/audit.py).
- **Sem credenciais** (testes, dev): `get_cos_sink()` retorna `None` silenciosamente; apenas o Cloudant é usado.
- O client `ibm_boto3` é **injetável** nos testes (sem rede real).
- Requisito de produção: COS com **Object Lock/retention (WORM)** nas cópias JSON; hash chain
  entre eventos para detectar adulteração.

### 5.3 GovernanceSink — Noop / watsonx.governance

| Implementação | Estado | Descrição |
|---|---|---|
| `NoopGovernanceSink` | **Ativo (Parte A)** | Descarta silenciosamente todos os registros |
| GovernanceSink para watsonx.governance | **Pendente** | Não implementado — ver seção 7. A instância `watsonx-Hackathon GOV` está provisionada na conta; falta o código, não o serviço |

### 5.4 Log JSON no stdout (Implementado)

- Logger: `desendividai.audit` (constante `AUDIT_EVENT_LOGGER_NAME`), nível INFO, `StreamHandler(sys.stdout)` próprio e `propagate=False`.
- Uma linha JSON por evento, com o registro **já sanitizado** pela guarda de PII (sem `_id`, `_rev` e `type`) e o campo extra `audit_persisted` (quantos destinos persistiram; `0` indica falha total).
- É o sinal consumido pelos alertas da seção 8. Não é trilha persistida: a fonte da verdade continua sendo Cloudant/COS.
- **Eventos só de log:** `emit_security_event()` escreve no mesmo logger sinais de segurança sem titular e de alto volume, que não devem encher o `audit_trail`. Hoje: `internal.auth_failed`, com `reason` (`missing_token` ou `invalid_token`), `method`, `route` (o molde da rota, ex. `/internal/erase/{pseudonym_id}`, nunca o caminho real) e `persisted=false`. Nunca contém o token recebido.

Exemplo:

```json
{"actor": "agent1", "audit_persisted": 1, "event_type": "vault.reveal", "ok": false, "pseudonym_id": "<hex-64>", "purpose": "SERASA_QUERY", "reason": "...", "recorded_at": "2026-11-06T12:00:00Z", "trace_id": "<uuid4>"}
```

---

## 6. Integração async→sync — `audit_bridge.py` (Implementado, Parte B)

O M3 usa `async` em toda a sua camada de serviço. O SDK de auditoria do M4 (`record_event`) é síncrono. A ponte [`src/privacy/audit_bridge.py`](../src/privacy/audit_bridge.py) resolve essa incompatibilidade:

- Chama `record_event` via `asyncio.to_thread`, sem bloquear o event loop do M3.
- Obtém o logger de auditoria de forma **tolerante**: se o `AuditLogger` não estiver configurado (ex.: `ConfigurationError` ao construir o repositório Cloudant), registra `logger.warning` e **não propaga a falha**. A requisição principal nunca é derrubada por falha de auditoria.
- Garantia: mesmo com o `audit_trail` indisponível, as respostas do Agente 2 (`/simulation`, `/recommendation`) continuam sendo entregues.

> **`vault.reveal` em produção:** a correção detectada na Parte B: `get_identity_vault()` criava o cofre **sempre** sem callback de auditoria (não apenas quando o repositório padrão não estava configurado). A função agora liga `get_audit_logger().record_event` via `_default_audit` tolerante, garantindo que eventos `vault.reveal` chegam ao `audit_trail` no Cloudant real mesmo em falhas parciais de configuração.

---

## 7. Pendente — watsonx.governance e AI Factsheets

> **Estado atual: nada é enviado ao watsonx.governance.** O sink padrão é `NoopGovernanceSink`, que descarta os registros. Isso é **pendência de implementação**, não limitação da conta: a instância `watsonx-Hackathon GOV` está provisionada (ver [`infra-setup.md`](infra-setup.md)). O passo 10 do fluxo do PLANO ("watsonx.governance registra trace completo") não está atendido.
>
> O trace do M3 (montado pelo Agente 2) já tem o que o AI Factsheets precisa: prompt, saída, modelo, regra de decisão e hashes. Falta um `GovernanceSink` em [`src/privacy/audit.py`](../src/privacy/audit.py) que envie esse trace, a ser feito em conjunto com o M3.

**Objetivo:** registrar cada sessão do Agente 1 como um evento de uso do modelo no AI Factsheets, permitindo rastreabilidade fim a fim da cadeia decisória.

**Configuração prevista:**

| Variável | Descrição |
|---|---|
| `WATSONX_GOVERNANCE_URL` | URL da instância watsonx.governance |
| `WATSONX_PROJECT_ID` | ID do projeto associado ao Agente 1 |

**Fluxo previsto:**

1. O GovernanceSink de produção (a implementar) recebe o registro de auditoria já sanitizado (sem PII) em `send(record)`.
2. O `trace_id` é usado como **chave de correlação** entre o evento de auditoria e a entrada no AI Factsheets.
3. O campo `tool_calls` do evento `agent1.snapshot_persisted` alimenta o rastreamento de quais ferramentas o modelo invocou.
4. Os campos `prompt_sha256`/`output_sha256` do evento `agent2.recommendation_generated` permitem correlacionar o trace com a inferência no watsonx.ai, sem expor o texto.
5. Autenticação via `IAMAuthenticator` com `IBM_CLOUD_API_KEY`.

---

## 8. Alertas de acesso anômalo (especificados, não ativos nesta conta)

### Mecanismo

Os eventos de auditoria saem como JSON no stdout pelo logger `desendividai.audit` (seção 5.4). Os alertas são regras sobre os campos dessas linhas: `event_type`, `ok`, `purpose`, `actor` e `audit_persisted`. Nenhum campo tem PII, então o pipeline de logs não precisa de tratamento especial.

### O que é possível nesta conta

- **Nem IBM Cloud Logs nem Activity Tracker estão provisionados** na conta do hackathon (catálogo restrito, ver [`infra-setup.md`](infra-setup.md)). Por isso **nenhum alerta está ativo**: os eventos ficam no `audit_trail` (Cloudant), nas cópias do COS e no stdout da aplicação, consultáveis sob demanda.
- Não há caminho suportado para levar os objetos do COS a um pipeline de alertas; a fonte dos alertas é o stdout, não o COS.
- **Em produção:** o stdout da aplicação é coletado pelo IBM Cloud Logs, onde as regras abaixo viram alertas com notificação (e-mail, Slack, PagerDuty).

### Alertas especificados

| Alerta | Regra sobre os logs de `desendividai.audit` | Severidade | Ação sugerida |
|---|---|---|---|
| Volume anômalo de `vault.reveal` | `event_type = "vault.reveal"` com mais de N linhas em T (ex.: 100 em 5 min), total ou por `actor` | CRÍTICO | Notificar DPO; suspender o actor |
| `vault.reveal` fora do padrão | `event_type = "vault.reveal"` e `purpose` fora de `SERASA_QUERY` / `LEGAL_REQUEST`; ou `ok = false` mais de M vezes em T | ALTO | Investigar comprometimento; revisar a trilha |
| `audit.pii_blocked` recorrente | `event_type = "audit.pii_blocked"` mais de P vezes em T para o mesmo `actor` | MÉDIO | Investigar bug ou tentativa de injeção |
| Trilha sem persistência | qualquer linha com `audit_persisted = 0` | ALTO | Cloudant/COS fora; verificar destinos de auditoria |
| Apagamentos em massa | `event_type = "consent.erasure_requested"` mais de E vezes em T | MÉDIO | Confirmar com o DPO que os pedidos são legítimos |
| Falhas de autenticação em `/internal/*` | `event_type = "internal.auth_failed"` mais de K vezes em T | ALTO | Rotacionar `INTERNAL_API_TOKEN`; bloquear a origem no gateway |
| Exportações em massa | `event_type = "subject.access_exported"` mais de X vezes em T | ALTO | Confirmar que os pedidos são legítimos (vazamento via endpoint interno) |
| Reveal negado por controle de acesso | `event_type = "vault.reveal"` e `reason` em `actor_not_allowed` / `no_active_consent` | ALTO | Investigar o chamador: código usando o cofre fora da finalidade |

Alertas que dependem de logs de plataforma (só produção, via Activity Tracker / Cloud Logs da conta BB):

| Alerta | Gatilho | Severidade |
|---|---|---|
| Leituras diretas em massa no `identity_vault` | Eventos de leitura do Cloudant no banco `identity_vault` fora da aplicação | CRÍTICO |
| Acesso à KEK fora do horário | Chamadas ao Secrets Manager fora do horário operacional | MÉDIO |

> Os limiares (N, T, M, P, E, K, X) devem ser calibrados pela equipe de segurança com base no volume real de operações.

---

## 9. Conformidade — o que o `audit_trail` nunca deve conter

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
| Ponte async→sync | [`src/privacy/audit_bridge.py`](../src/privacy/audit_bridge.py) |
| Guarda de PII | [`src/privacy/pii_guard.py`](../src/privacy/pii_guard.py) |
| Integração no persist_snapshot | [`src/agents/agent1/tools.py`](../src/agents/agent1/tools.py) |
| Integração no cofre (vault.reveal) | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Evento retention.run | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Eventos erasure | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
