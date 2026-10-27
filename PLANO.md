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
**Status:** [x] done

#### Intent
Definir como o cliente autoriza o acesso aos seus dados financeiros via Open Finance e como esse consentimento é validado em cada chamada ao gateway.

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

#### Entregáveis
- [`openapi/openapi.yaml`](openapi/openapi.yaml) — Contrato OpenAPI 3.0 completo
- [`docs/m1-consent-flow.md`](docs/m1-consent-flow.md) — Diagrama de sequência OAuth2 FAPI
- [`docs/m1-api-connect-policies.md`](docs/m1-api-connect-policies.md) — Políticas API Connect
- [`docs/m1-consent-lifecycle.md`](docs/m1-consent-lifecycle.md) — Ciclo de vida do consentimento

#### Relevant Context
- Regulação: Open Finance Brasil — [openfinancebrasil.org.br](https://openfinancebrasil.org.br)
- Gateway: IBM API Connect (`docs_ibm_api_connect_cloud`)
- Auth: IBM Cloud IAM (`docs_ibm_cloud_iam`)
- Padrão OAuth2 FAPI (Financial-grade API) exigido pelo Banco Central

---

### Sub-Tarefa 2 — Agente 1: Coleta, Análise e Priorização por CET
**Responsável:** M2
**Status:** [ ] pending

#### Intent
Projetar o Agente 1 responsável por coletar dívidas via Open Finance e Serasa, calcular o CET de cada dívida e montar o retrato financeiro priorizado do cliente.

#### Expected Outcomes
- Arquitetura do Agente 1 definida (LangChain AgentExecutor + Granite via watsonx.ai)
- Tools definidas: `get_open_finance_debts`, `get_serasa_data`, `calculate_cet`, `persist_snapshot`
- Lógica de cálculo de CET especificada (fórmula, campos necessários de cada dívida)
- Schema do documento MongoDB para snapshot financeiro pseudonimizado

#### Todo List
- [ ] Definir modelo Granite a ser usado (ex: `ibm/granite-3-8b-instruct`) via watsonx.ai API
- [ ] Especificar integração LangChain com watsonx.ai (IBM watsonx LLM wrapper)
- [ ] Definir as 4 tools do Agente 1 com assinaturas de entrada/saída
- [ ] Especificar fórmula de cálculo do CET e campos obrigatórios da API Open Finance
- [ ] Definir critérios de priorização (ordenação decrescente por CET)
- [ ] Projetar schema do documento MongoDB: campos, índices, campo `pseudonym_id`
- [ ] Definir estratégia de pseudonimização: geração do `pseudonym_id` antes da persistência
- [ ] Documentar prompt template do Agente 1 (system prompt + tool descriptions)

#### Relevant Context
- LangChain + watsonx.ai: [IBM watsonx LangChain integration](https://python.langchain.com/docs/integrations/llms/ibm_watsonx/)
- CET: regulação BCB Resolução nº 3.517/2007
- MongoDB: `docs_ibm_cloud_databases_for_mongodb`
- Pseudonimização: ver Sub-Tarefa 4 (M4)

---

### Sub-Tarefa 3 — Agente 2: Simulação de Cenários e Recomendação
**Responsável:** M3
**Status:** [ ] pending

#### Intent
Projetar o Agente 2 responsável por simular cenários de quitação e gerar a recomendação final em linguagem simples, usando o Motor de Simulação como AI Service no watsonx.ai.

#### Expected Outcomes
- Arquitetura do Agente 2 definida (LangChain + Granite)
- Motor de Simulação especificado como AI Service Python no watsonx.ai
- 3 cenários de simulação definidos com inputs/outputs claros
- Formato de resposta final especificado (JSON estruturado + explicação textual)
- Orquestração LangChain entre Agente 1 → Agente 2 especificada

#### Todo List
- [ ] Definir interface entre Agente 1 e Agente 2 (schema do snapshot como input)
- [ ] Especificar os 3 cenários do Motor de Simulação:
  - **Agressivo:** máxima quitação com orçamento disponível, maior CET primeiro
  - **Consolidação:** portabilidade de dívidas caras para linha BB com CET menor
  - **Mínimo viável:** apenas pagamento mínimo + quitação da dívida mais cara
- [ ] Definir inputs/outputs do Motor de Simulação (API contract do AI Service)
- [ ] Especificar deploy do Motor como AI Service no watsonx.ai (endpoint REST)
- [ ] Definir tool `run_simulation` do Agente 2 que chama o AI Service
- [ ] Projetar prompt template do Agente 2 para geração de recomendação em linguagem simples
- [ ] Definir schema JSON de resposta final: `scenarios[]`, `recommended_scenario`, `explanation`
- [ ] Especificar como o Agente 2 é invocado após conclusão do Agente 1 (LangChain chain)

#### Relevant Context
- AI Services watsonx.ai: [Deploying AI services](https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/ai-services-direct-coding-overview.html)
- LangChain chain composition: SequentialChain ou LCEL (LangChain Expression Language)
- Motor de Simulação: lógica financeira pura em Python (sem LLM), deployada como AI Service

---

### Sub-Tarefa 4 — Dados, Pseudonimização, HSM e Governança LGPD
**Responsável:** M4
**Status:** [ ] pending

#### Intent
Projetar a camada de dados garantindo que informações pessoais financeiras sejam pseudonimizadas antes de qualquer persistência, com chave de re-identificação segregada em HSM, e que toda operação seja auditável para conformidade com a LGPD.

#### Expected Outcomes
- Estratégia de pseudonimização definida e documentada
- Arquitetura do MongoDB com separação de coleções sensíveis e pseudonimizadas
- Fluxo de gestão da chave de re-identificação no IBM Cloud Secrets Manager + HSM
- Política de retenção e deleção de dados (direito ao esquecimento — LGPD Art. 18)
- Configuração do watsonx.governance para rastreamento de decisões dos agentes
- Mapeamento de dados pessoais (ROPA — Record of Processing Activities)

#### Todo List
- [ ] Mapear todos os campos pessoais/sensíveis processados (CPF, saldo, dívidas, renda)
- [ ] Definir algoritmo de pseudonimização: HMAC-SHA256 com chave mestra no HSM
- [ ] Projetar separação no MongoDB:
  - Coleção `financial_snapshots` — apenas dados pseudonimizados
  - Coleção `consent_registry` — registro de consentimentos (sem dados financeiros)
- [ ] Especificar fluxo de acesso à chave HSM: qual serviço acessa, quando, com qual role IAM
- [ ] Definir política de retenção: TTL por tipo de dado, processo de deleção segura
- [ ] Mapear como o watsonx.governance registra cada decisão dos agentes (trace de auditoria)
- [ ] Documentar ROPA (mapeamento de tratamento de dados pessoais) conforme LGPD
- [ ] Definir alertas de IBM Cloud Logs para acessos anômalos aos dados sensíveis

#### Relevant Context
- IBM Cloud Secrets Manager: `docs_ibm_cloud_secrets_manager`
- watsonx.governance: `docs_ibm_watsonx` (seção governance/AI Factsheets)
- LGPD: Lei nº 13.709/2018 — especialmente Art. 18 (direitos do titular) e Art. 46 (segurança)
- IBM Cloud IAM: `docs_ibm_cloud_iam` — trusted profiles para acesso ao HSM
- MongoDB TTL indexes para expiração automática de documentos

---

## Decisões Técnicas Registradas

| Decisão | Escolha | Justificativa |
|---|---|---|
| Orquestração de agentes | LangChain | Flexibilidade, integração nativa com watsonx.ai, sem lock-in em plataforma IBM |
| LLM backend | watsonx.ai Granite | Modelo IBM, soberania de dados, conformidade com requisitos bancários |
| Armazenamento | IBM Cloud Databases for MongoDB | NoSQL flexível para snapshots financeiros semi-estruturados |
| Pseudonimização | HMAC-SHA256 + HSM | Pseudonimização determinística (permite correlação interna) com chave segregada |
| Gateway | IBM API Connect | Padrão IBM, suporte a FAPI (Financial-grade API), políticas OAuth2 nativas |
| Governança | watsonx.governance | Rastreabilidade de decisões de IA exigida por regulação financeira |
| Motor de simulação | AI Service Python no watsonx.ai | Lógica financeira pura, sem dependência de LLM, reutilizável como endpoint REST |
| Canal de engajamento | Fora do escopo | API genérica OpenAPI — qualquer canal pode integrar |
| Acompanhamento mensal | Fora do escopo | Reduz complexidade da v1 |
| Negociação automatizada | Fora do escopo | Risco regulatório, requer aprovações adicionais |
