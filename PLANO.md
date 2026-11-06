# DesendividAÍ — Plano de Arquitetura

## Visão Geral

**Objetivo:** Projetar a arquitetura técnica do DesendividAÍ, um agente de IA do Banco do Brasil que consolida dívidas de múltiplas instituições via Open Finance e Serasa, prioriza pelo CET (Custo Efetivo Total), simula cenários de quitação e entrega um plano personalizado via API REST.

**Abordagem:** Arquitetura multiagente com LangChain + watsonx.ai (Granite), armazenamento NoSQL pseudonimizado, gateway REST via IBM API Connect e governança LGPD via watsonx.governance.

**Fora do escopo:**
- UI / canal de engajamento (API genérica OpenAPI)
- Acompanhamento mensal do cliente
- Negociação automatizada com plataformas de acordo
- Data lakehouse

**Responsáveis:** 4 membros da equipe (M1, M2, M3, M4)

---

## Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────┐
│                        Canal do Cliente                         │
│          (app BB / WhatsApp / portal — fora do escopo)          │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────────┐
│                   IBM API Connect                               │
│         Gateway REST · OpenAPI/Swagger · Auth (OAuth2)          │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│              Camada de Orquestração (LangChain)                 │
│                                                                 │
│  ┌──────────────────────────┐  ┌──────────────────────────┐    │
│  │   Agente 1               │  │   Agente 2               │    │
│  │   Análise & Priorização  │─▶│   Simulação & Reco-      │    │
│  │   (LangChain + Granite)  │  │   mendação               │    │
│  │                          │  │   (LangChain + Granite)  │    │
│  └──────────┬───────────────┘  └──────────┬───────────────┘    │
│             │                             │                     │
│      Tools  │                      Tools  │                     │
│  ┌──────────▼───────┐        ┌────────────▼──────────┐         │
│  │ Open Finance API │        │ Motor de Simulação    │         │
│  │ Serasa API       │        │ (AI Service Python)   │         │
│  │ Consent Manager  │        │ watsonx.ai deploy     │         │
│  └──────────────────┘        └───────────────────────┘         │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│              Camada de Dados                                    │
│                                                                 │
│  ┌────────────────────────┐   ┌──────────────────────────────┐ │
│  │ IBM Cloud Databases    │   │ IBM Cloud Secrets Manager    │ │
│  │ for MongoDB            │   │ + HSM                        │ │
│  │ (dados pseudonimizados)│   │ (chave de re-identificação)  │ │
│  └────────────────────────┘   └──────────────────────────────┘ │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│              Governança & Observabilidade                       │
│   watsonx.governance · IBM Cloud Logs · IBM Cloud IAM           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Fluxo de Dados Principal

```
1. Cliente autoriza consentimento (OAuth2 Open Finance)
2. API Connect recebe requisição → valida token IAM
3. Agente 1 chama tools: Open Finance API + Serasa API
4. Dados brutos chegam → pseudonimização antes de persistir no MongoDB
5. Agente 1 calcula CET de cada dívida → prioriza → persiste snapshot
6. Agente 2 recebe snapshot → chama Motor de Simulação (AI Service)
7. Motor retorna 3 cenários: agressivo / consolidação / portabilidade
8. Agente 2 gera recomendação em linguagem simples (Granite)
9. API Connect retorna resposta JSON ao canal do cliente
10. watsonx.governance registra trace completo para auditoria LGPD
```

---

## Sub-Tarefas

---

### Sub-Tarefa 1 — Fluxo de Consentimento e Gateway REST
**Responsável:** M1
**Status:** [x] done (design + implementação dos endpoints — branch `feat/m1-consentimento`)

#### Intent
Definir como o cliente autoriza o acesso aos seus dados financeiros via Open Finance e como esse consentimento é validado em cada chamada ao gateway. Implementar os 4 endpoints de consentimento que estavam como stubs.

#### Expected Outcomes
- Contrato OpenAPI/Swagger documentando todos os endpoints da solução
- Fluxo de consentimento OAuth2 desenhado (sequência de chamadas)
- Política de autenticação configurada no IBM API Connect
- Decisão documentada sobre ciclo de vida do token de consentimento (expiração, revogação)

#### Todo List
- [x] Estudar especificação Open Finance Brasil (fase 2 — dados de crédito)
- [x] Mapear endpoints necessários: `/consent`, `/debts`, `/simulation`, `/recommendation`
- [x] Desenhar diagrama de sequência do fluxo de consentimento OAuth2
- [x] Definir contrato OpenAPI 3.0 com schemas de request/response para cada endpoint
- [x] Especificar política de rate limiting, autenticação e CORS no IBM API Connect
- [x] Documentar ciclo de vida do consentimento (TTL, revogação, re-autorização)
- [x] Implementar `POST /consent`, `GET /consent/callback`, `GET /consent/{id}`, `DELETE /consent/{id}`

#### Entregáveis de design
- [`openapi/openapi.yaml`](openapi/openapi.yaml) — Contrato OpenAPI 3.0 completo
- [`docs/m1-consent-flow.md`](docs/m1-consent-flow.md) — Diagrama de sequência OAuth2 FAPI
- [`docs/m1-api-connect-policies.md`](docs/m1-api-connect-policies.md) — Políticas API Connect
- [`docs/m1-consent-lifecycle.md`](docs/m1-consent-lifecycle.md) — Ciclo de vida do consentimento

#### Entregáveis de código
- [`src/services/consent_service.py`](src/services/consent_service.py) — regras, validação, CPF no cofre de identidade do M4, eventos `consent.*` na trilha de auditoria do M4, transições com escrita condicional por `_rev`, cliente de revogação de token
- [`src/api/routers/consent.py`](src/api/routers/consent.py) — 4 rotas implementadas (POST, GET /callback, GET /{id}, DELETE)
- [`src/api/models.py`](src/api/models.py) — `ConsentStatusResponse`; `Field(example=…)` → `json_schema_extra`; `cpf` pattern ASCII-only
- [`src/main.py`](src/main.py) — handler de `RequestValidationError` para `/consent` e `/consent/*` (400 sem ecoar CPF)
- [`src/storage/store.py`](src/storage/store.py) — `put(..., rev=...)`: escrita condicional (409 `CONFLICT` se outra escrita chegou antes); sem `rev`, comportamento anterior
- [`tests/test_consent_api.py`](tests/test_consent_api.py) — 72 casos de teste (create, callback, status, revoke, concorrência, cofre e auditoria, integração M2/M3, lgpd, openapi contract)
- [`.env.example`](.env.example) — bloco M1 (`OPEN_FINANCE_AUTH_SANDBOX`, `PUBLIC_BASE_URL`, `OPEN_FINANCE_AUTHORIZE_URL`, `OPEN_FINANCE_CLIENT_ID`, `OPEN_FINANCE_REDIRECT_URI`, `OAUTH_REVOKE_URL`)
- [`scripts/cloud_setup.py`](scripts/cloud_setup.py) — índices `by-state-hash` e `by-consent-id` em `consent_registry`

#### Pendências que continuam abertas
| Item | Onde |
|---|---|
| FAPI completo: PKCE S256, request object JWT (PS256), mTLS/DPoP, troca `code → token` | API Connect (configuração) |
| Introspecção do token e validação de escopo no gateway | API Connect |
| Vincular titular do token a `snapshot_id`/`simulation_id` (IDOR registrado pelo M3) | API Connect + M3 |
| Afinidade por `consent_id` no gateway (jobs `202` do `GET /debts` em memória) | API Connect |
| Rate limit (`429`) e `401` | API Connect |
| Ligar `REQUIRE_CONSENT=true` por padrão e aposentar `scripts/seed_test_consent.py` | Decisão do time após o merge |
| Contrato exato do endpoint de revogação de token do IBM API Connect (B3); revogar também no erase LGPD e na expiração (regra c) | API Connect + M4 |
| Unificar o código 403 de consentimento não `AUTHORISED`: `CONSENT_NOT_AUTHORISED` no `GET /debts` × `CONSENT_REQUIRED` no `/simulation` e `/recommendation` (o contrato documenta os dois) | M2 + M3 |

#### Relevant Context
- Regulação: Open Finance Brasil — [openfinancebrasil.org.br](https://openfinancebrasil.org.br)
- Gateway: IBM API Connect (`docs_ibm_api_connect_cloud`)
- Auth: IBM Cloud IAM (`docs_ibm_cloud_iam`)
- Padrão OAuth2 FAPI (Financial-grade API) exigido pelo Banco Central

---

### Sub-Tarefa 2 — Agente 1: Coleta, Análise e Priorização por CET
**Responsável:** M2
**Status:** [x] done — pendências que dependem de M1, M3 e M4 listadas em [`docs/m2-agent1.md`](docs/m2-agent1.md#12-pendências)

#### Intent
Projetar o Agente 1 responsável por coletar dívidas via Open Finance e Serasa, calcular o CET de cada dívida e montar o retrato financeiro priorizado do cliente.

#### Expected Outcomes
- Arquitetura do Agente 1 definida (LangChain AgentExecutor + Granite via watsonx.ai)
- Tools definidas: `get_open_finance_debts`, `get_serasa_data`, `calculate_cet`, `persist_snapshot`
- Lógica de cálculo de CET especificada (fórmula, campos necessários de cada dívida)
- Schema do documento MongoDB para snapshot financeiro pseudonimizado

#### Todo List
- [x] Definir modelo Granite a ser usado (ex: `ibm/granite-3-8b-instruct`) via watsonx.ai API — adotado `ibm/granite-4-h-small` (o 3-8b não é mais suportado em us-south)
- [x] Especificar integração LangChain com watsonx.ai (IBM watsonx LLM wrapper)
- [x] Definir as 4 tools do Agente 1 com assinaturas de entrada/saída
- [x] Especificar fórmula de cálculo do CET e campos obrigatórios da API Open Finance
- [x] Definir critérios de priorização (ordenação decrescente por CET)
- [x] Projetar schema do documento MongoDB: campos, índices, campo `pseudonym_id`
- [x] Definir estratégia de pseudonimização: geração do `pseudonym_id` antes da persistência
- [x] Documentar prompt template do Agente 1 (system prompt + tool descriptions)

#### Entregáveis
- [`docs/m2-agent1.md`](docs/m2-agent1.md) — Arquitetura, tools, fórmula do CET, priorização, schema do snapshot, pseudonimização e prompt
- [`src/agents/agent1/`](src/agents/agent1/) — Implementação do Agente 1 (LangChain `AgentExecutor` + Granite via watsonx.ai)
- [`src/privacy/pseudonymization.py`](src/privacy/pseudonymization.py) — HMAC-SHA256 compartilhado com M1/M4
- [`src/api/routers/debts.py`](src/api/routers/debts.py) — `GET /debts` implementado
- [`scripts/seed_test_consent.py`](scripts/seed_test_consent.py) — Atalho de teste: cria/remove um consentimento AUTHORISED no Cloudant (o caminho normal é `POST /consent` + callback)
- [`scripts/update_market_rates.py`](scripts/update_market_rates.py) — Atualiza as taxas médias PF do BCB usadas para estimar CET de dívidas só do Serasa
- [`tests/`](tests/) — Testes de CET, priorização, pseudonimização, agente e endpoint

#### Relevant Context
- LangChain + watsonx.ai: [IBM watsonx LangChain integration](https://python.langchain.com/docs/integrations/llms/ibm_watsonx/)
- CET: regulação BCB Resolução nº 3.517/2007
- MongoDB: `docs_ibm_cloud_databases_for_mongodb`
- Pseudonimização: ver Sub-Tarefa 4 (M4)

---

### Sub-Tarefa 3 — Agente 2: Simulação de Cenários e Recomendação
**Responsável:** M3
**Status:** [x] done

#### Intent
Projetar o Agente 2 responsável por simular cenários de quitação e gerar a recomendação final em linguagem simples, usando o Motor de Simulação como AI Service no watsonx.ai.

#### Expected Outcomes
- Arquitetura do Agente 2 definida (LangChain + Granite)
- Motor de Simulação especificado como AI Service Python no watsonx.ai
- 3 cenários de simulação definidos com inputs/outputs claros
- Formato de resposta final especificado (JSON estruturado + explicação textual)
- Orquestração LangChain entre Agente 1 → Agente 2 especificada

#### Todo List
- [x] Definir interface entre Agente 1 e Agente 2 (schema do snapshot como input)
- [x] Especificar os 3 cenários do Motor de Simulação:
  - **Agressivo:** máxima quitação com orçamento disponível, maior CET primeiro
  - **Consolidação:** portabilidade de dívidas caras para linha BB com CET menor
  - **Mínimo viável:** apenas pagamento mínimo + quitação da dívida mais cara
- [x] Definir inputs/outputs do Motor de Simulação (API contract do AI Service)
- [x] Especificar deploy do Motor como AI Service no watsonx.ai (endpoint REST)
- [x] Definir tool `run_simulation` do Agente 2 que chama o AI Service
- [x] Projetar prompt template do Agente 2 para geração de recomendação em linguagem simples
- [x] Definir schema JSON de resposta final: `scenarios[]`, `recommended_scenario`, `explanation`
- [x] Especificar como o Agente 2 é invocado após conclusão do Agente 1 (LangChain chain)

#### Entregáveis
- [`src/simulation/engine.py`](src/simulation/engine.py) — Motor de Simulação (3 cenários), com [`ai_service.py`](src/simulation/ai_service.py) e [`client.py`](src/simulation/client.py)
- [`src/agents/recommendation_agent.py`](src/agents/recommendation_agent.py) — Agente 2 (LCEL + Granite) e tool `run_simulation`
- `POST /simulation` e `POST /recommendation` implementados; testes em `tests/`
- [`docs/m3-simulation-engine.md`](docs/m3-simulation-engine.md) — contrato, cenários e premissas
- [`docs/m3-agent2-recommendation.md`](docs/m3-agent2-recommendation.md) — prompt, chain, governança

> Desvios: o cenário recomendado é escolhido por regra determinística (o LLM só explica); o deploy do AI Service no watsonx.ai e o envio do trace ao watsonx.governance ainda não foram executados.

#### Relevant Context
- AI Services watsonx.ai: [Deploying AI services](https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/ai-services-direct-coding-overview.html)
- LangChain chain composition: SequentialChain ou LCEL (LangChain Expression Language)
- Motor de Simulação: lógica financeira pura em Python (sem LLM), deployada como AI Service

---

### Sub-Tarefa 4 — Dados, Pseudonimização, HSM e Governança LGPD
**Responsável:** M4
**Status:** Parte A e Parte B concluídas

#### Intent
Projetar a camada de dados garantindo que informações pessoais financeiras sejam pseudonimizadas antes de qualquer persistência, com chave de re-identificação segregada em HSM, e que toda operação seja auditável para conformidade com a LGPD.

#### Expected Outcomes
- Estratégia de pseudonimização definida e documentada
- Arquitetura do Cloudant com separação de coleções sensíveis e pseudonimizadas
- Fluxo de gestão da chave de re-identificação (hackathon: env + `KeyProvider`; produção: Secrets Manager + HSM, Trusted Profile)
- Política de retenção e deleção de dados via job de expurgo idempotente (direito ao esquecimento — LGPD Art. 18)
- Trilha de auditoria append-only pela aplicação em `audit_trail` + COS (`audit/`) (o código só insere; não há update/delete; para proteção plena em produção: COS com Object Lock/WORM, hash chain e credencial exclusiva de escrita)
- Mapeamento de dados pessoais (ROPA — Record of Processing Activities)

#### Todo List
- [x] Mapear todos os campos pessoais/sensíveis processados (CPF, saldo, dívidas, renda)
- [x] Definir algoritmo de pseudonimização: HMAC-SHA256 com chave mestra via `KeyProvider`
- [x] Projetar separação no Cloudant:
  - Coleção `financial_snapshots` — apenas dados pseudonimizados
  - Coleção `consent_registry` — registro de consentimentos (sem dados financeiros)
  - Coleção `identity_vault` — CPF cifrado com DEK por titular + envelope KEK
  - Coleção `audit_trail` — eventos e traces (nunca expurgada)
- [x] Especificar fluxo de acesso à chave: `EnvKeyProvider` (hackathon); produção deve implementar um KeyProvider sobre o Secrets Manager com role IAM `SecretsReader`
- [x] Definir política de retenção: job de expurgo idempotente (`src/privacy/retention.py`) sobre `expires_at` / `awaiting_expires_at`
- [x] Registrar cada operação sobre dados do titular na trilha de auditoria (`src/privacy/audit.py`); produção deve implementar um GovernanceSink para o watsonx.governance (plano)
- [x] Documentar ROPA (mapeamento de tratamento de dados pessoais) conforme LGPD
- [x] Definir alertas de acesso anômalo — especificados em `docs/m4-auditoria-governanca.md` (plano / produção)
- [x] Ligar `resolve_for_serasa` ao cliente Serasa real (Parte B — `HttpSerasaClient` real usa `resolver` injetável; CPF apenas no corpo do POST)
- [x] Retenção e erase cobrem `decision_traces` (regra a2, campo `decisions_deleted`) — Parte B
- [x] Guarda de PII e auditoria no Agente 2 (`agent2.simulation_created`, `agent2.recommendation_generated`, hashes `prompt_sha256`/`output_sha256`) — Parte B
- [x] `src/privacy/audit_bridge.py` (ponte async→sync, tolerante) — Parte B
- [x] Retry com backoff exponencial para 429 do Cloudant Lite (`_with_429_retry`) — Parte B
- [x] `vault.reveal` ligado em produção (corrigido `_default_audit` tolerante) — Parte B

#### Entregáveis
- [`src/privacy/key_provider.py`](src/privacy/key_provider.py) — `KeyProvider`, `EnvKeyProvider`
- [`src/privacy/pseudonymization.py`](src/privacy/pseudonymization.py) — HMAC-SHA256 + `current_key_version()` + `pseudonymize_cpf_with_version()`
- [`src/privacy/identity_vault.py`](src/privacy/identity_vault.py) — cofre AES-256-GCM com DEK por titular; `store`, `reveal` (auditado), `shred`, `resolve_for_serasa`
- [`src/privacy/pii_guard.py`](src/privacy/pii_guard.py) — `assert_no_pii()` recursivo; detecta CPF em 4 formatos (0 falsos positivos em 5.000 textos financeiros)
- [`src/privacy/audit.py`](src/privacy/audit.py) — `record_event()`, `AuditRepository`, cópia COS, `GovernanceSink`
- [`src/privacy/audit_bridge.py`](src/privacy/audit_bridge.py) — ponte async→sync para o M3 (Parte B)
- [`src/privacy/retention.py`](src/privacy/retention.py) — `run_retention()`, `ensure_indexes()`, `ensure_databases()`; CLI `python -m src.privacy.retention [--dry-run] [--ensure]`; regra a2 para `decision_traces` (Parte B)
- [`src/privacy/retention_store.py`](src/privacy/retention_store.py) — repositórios de retenção; retry 429 (`_with_429_retry`); `iter_decisions`, `delete_decision` (Parte B)
- [`src/privacy/erasure.py`](src/privacy/erasure.py) — lógica de apagamento LGPD Art. 18; `decisions_deleted` (Parte B)
- [`src/api/routers/internal.py`](src/api/routers/internal.py) — `POST /internal/erase/{pseudonym_id}`, `POST /internal/retention/run`
- [`tests/m4/`](tests/m4/) — testes unitários e de integração da camada de privacidade
- [`docs/m4-mapeamento-dados.md`](docs/m4-mapeamento-dados.md) — mapeamento de todos os campos pessoais/financeiros (inclui `decision_traces`)
- [`docs/m4-ropa.md`](docs/m4-ropa.md) — ROPA LGPD
- [`docs/m4-retencao-e-delecao.md`](docs/m4-retencao-e-delecao.md) — política de retenção e deleção
- [`docs/m4-chaves-e-iam.md`](docs/m4-chaves-e-iam.md) — gestão de chaves e IAM
- [`docs/m4-auditoria-governanca.md`](docs/m4-auditoria-governanca.md) — auditoria e governança
- `TEST_FINAL.md` — relatório de testes final (gerado pelo orquestrador)

#### Relevant Context
- IBM Cloud Secrets Manager: `docs_ibm_cloud_secrets_manager`
- watsonx.governance: `docs_ibm_watsonx` (seção governance/AI Factsheets)
- LGPD: Lei nº 13.709/2018 — especialmente Art. 18 (direitos do titular) e Art. 46 (segurança)
- IBM Cloud IAM: `docs_ibm_cloud_iam` — trusted profiles para acesso ao HSM
- Cloudant sem TTL nativo — job de expurgo substitui os índices TTL do MongoDB

---

## Decisões Técnicas Registradas

| Decisão | Escolha | Justificativa |
|---|---|---|
| Orquestração de agentes | LangChain | Flexibilidade, integração nativa com watsonx.ai, sem lock-in em plataforma IBM |
| LLM backend | watsonx.ai Granite | Modelo IBM, soberania de dados, conformidade com requisitos bancários |
| Armazenamento (produção) | IBM Cloud Databases for MongoDB | NoSQL flexível para snapshots financeiros semi-estruturados |
| Armazenamento (hackathon) | **Cloudant** (substituto) | MongoDB indisponível na conta do hackathon; Cloudant é document-store compatível |
| Secrets (produção) | IBM Cloud Secrets Manager + HSM | Pseudonimização determinística com chave segregada em hardware |
| Secrets (hackathon) | **Env + KeyProvider; envelope encryption com DEK por titular** | Secrets Manager indisponível na conta do hackathon; chaves via variáveis de ambiente atrás da interface `KeyProvider`; CPF cifrado com AES-256-GCM e DEK aleatória por titular, envolvida pela KEK |
| Pseudonimização | HMAC-SHA256 + chave mestra | Pseudonimização determinística (permite correlação interna) |
| Retenção no Cloudant | Job de expurgo idempotente (`src/privacy/retention.py`) | Cloudant não tem TTL nativo; job substitui os índices TTL do MongoDB previstos no M1 |
| Gateway (produção) | IBM API Connect | Padrão IBM, suporte a FAPI (Financial-grade API), políticas OAuth2 nativas |
| Gateway (hackathon) | **Não provisionado** — contrato em `openapi/openapi.yaml`, políticas em `docs/m1-api-connect-policies.md` | API Connect fora da conta do hackathon. Sem gateway, nada valida token: o backend confia que só o gateway o chama. Canal usa client credentials (`consent:*`); titular usa authorization code + PKCE (dados) |
| Governança | watsonx.governance | Rastreabilidade de decisões de IA exigida por regulação financeira |
| Motor de simulação | AI Service Python no watsonx.ai | Lógica financeira pura, sem dependência de LLM, reutilizável como endpoint REST |
| Canal de engajamento | Fora do escopo | API genérica OpenAPI — qualquer canal pode integrar |
| Acompanhamento mensal | Fora do escopo | Reduz complexidade da v1 |
| Negociação automatizada | Fora do escopo | Risco regulatório, requer aprovações adicionais |
