# DesendividAÍ

> Assistente de inteligência financeira para desendividamento, construído sobre o ecossistema IBM watsonx.

Vídeo de demonstração (3 min, narrado): [`docs/video/desendividai-demo.mp4`](docs/video/desendividai-demo.mp4) · Resumos das sessões do IBM Bob: [`bob_sessions/`](bob_sessions/)

---

## Visão Geral

O **DesendividAÍ** é uma solução de IA multiagente voltada a clientes do Banco do Brasil que enfrentam dívidas em múltiplos credores. O sistema agrega dados do Open Finance Brasil, analisa o Custo Efetivo Total (CET) de cada dívida, simula estratégias de quitação/portabilidade e negocia condições diretamente com plataformas parceiras — tudo com linguagem acessível, explicabilidade auditável e conformidade com LGPD e Bacen.

---

## Como rodar

API FastAPI com os quatro módulos do [`PLANO.md`](PLANO.md): consentimento (M1), Agente 1 de coleta e CET (M2), simulação e recomendação (M3), dados e LGPD (M4). Open Finance e Serasa rodam em **sandbox** (dados fictícios); Cloudant, cofre de identidade, trilha de auditoria e Granite são **reais**.

### 1. Pré-requisitos

| Item | Versão / acesso |
|---|---|
| Python | 3.11 a 3.13 — o `langchain-ibm` ainda não instala no 3.14 |
| Conta IBM Cloud | acesso à conta do hackathon (`63520643c32b4588b4c6f7d8546cf66e`) e ao grupo `watsonx-Hackathon-Access` |
| IBM Cloud CLI | opcional, para gerar as credenciais abaixo |

### 2. Instalação

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 3. Configuração do `.env`

Mínimo para o fluxo completo:

| Variável | Valor | De onde vem |
|---|---|---|
| `CLOUDANT_URL`, `CLOUDANT_APIKEY` | URL e API key do Cloudant | credencial `desendividai-cloudant-key`: `ibmcloud resource service-key desendividai-cloudant-key` |
| `IBM_CLOUD_API_KEY` | API key **pessoal** de quem roda | `ibmcloud iam api-key-create <nome> --file key.json` |
| `WATSONX_URL` | `https://us-south.ml.cloud.ibm.com` | — |
| `WATSONX_PROJECT_ID` | ID do projeto watsonx do time | Watson Studio → projeto → Manage → General |
| `WATSONX_MODEL_ID` | `ibm/granite-4-h-small` | — |
| `PSEUDONYM_HMAC_KEY`, `VAULT_KEK_V1` | 64 caracteres hex cada | **as mesmas do time** (pedir a quem já tem) |
| `VAULT_KEK_ACTIVE_VERSION`, `PSEUDONYM_KEY_ACTIVE_VERSION` | `v1` | — |
| `OPEN_FINANCE_SANDBOX`, `SERASA_SANDBOX`, `OPEN_FINANCE_AUTH_SANDBOX` | `true` | — |
| `APP_ENV` | `development` | — |
| `INTERNAL_API_TOKEN` | qualquer segredo longo | só para `/internal/*` (apagamento LGPD) |

A API key do watsonx precisa ser pessoal: o Service ID `desendividai-agents` não é membro do projeto nem do grupo de acesso, e só os organizadores podem incluí-lo. `PSEUDONYM_HMAC_KEY` e `VAULT_KEK_V1` têm que ser iguais para todo o time — com chaves diferentes o mesmo CPF vira pseudônimos diferentes e o cofre de um não abre com a chave do outro. Gerar novas só num ambiente novo:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Opcional: `COS_ACCESS_KEY_ID`, `COS_SECRET_ACCESS_KEY`, `COS_BUCKET_NAME`, `COS_BUCKET_REGION` (credencial HMAC `desendividai-hmac`). Sem COS, a trilha de auditoria grava só no Cloudant. As demais variáveis estão comentadas no [`.env.example`](.env.example).

### 4. Preparar o Cloudant (uma vez por conta)

```bash
python -m scripts.cloud_setup
```

Cria os bancos `consent_registry`, `financial_snapshots`, `decision_traces`, `identity_vault`, `audit_trail` e os índices do M1. Idempotente.

### 5. Subir a API

```bash
uvicorn src.main:app --reload
```

| Endereço | O que é |
|---|---|
| `http://localhost:8000/demo/` | interface de demonstração do fluxo (só fora de produção) |
| `http://localhost:8000/docs` | Swagger da API |
| `http://localhost:8000/health` | health check |

### 6. Fluxo pela linha de comando

CPF `52998224725` é fictício (dados de sandbox).

```bash
API=http://localhost:8000
R=$(curl -s -X POST $API/consent -H 'Content-Type: application/json' \
  -d '{"cpf":"52998224725","institutions":["bb","itau","nubank"],"scopes":["debts:read","simulation:read","recommendation:read"]}')
CID=$(echo "$R" | python -c 'import sys,json;print(json.load(sys.stdin)["consent_id"])')
STATE=$(echo "$R" | python -c 'import sys,json,urllib.parse as u;print(u.parse_qs(u.urlparse(json.load(sys.stdin)["redirect_url"]).query)["state"][0])')

curl -s "$API/consent/callback?state=$STATE&code=sandbox"            # → AUTHORISED
until D=$(curl -s -w '\n%{http_code}' "$API/debts?consent_id=$CID") && [ "$(echo "$D" | tail -1)" = 200 ]; do sleep 2; done  # 202 enquanto o Agente 1 roda
SID=$(echo "$D" | head -1 | python -c 'import sys,json;print(json.load(sys.stdin)["snapshot_id"])')
SIM=$(curl -s -X POST $API/simulation -H 'Content-Type: application/json' -d "{\"snapshot_id\":\"$SID\",\"monthly_budget\":4000}")
SIMID=$(echo "$SIM" | python -c 'import sys,json;print(json.load(sys.stdin)["simulation_id"])')
curl -s -X POST $API/recommendation -H 'Content-Type: application/json' -d "{\"simulation_id\":\"$SIMID\"}"
curl -s -X DELETE $API/consent/$CID -o /dev/null -w '%{http_code}\n'  # 204; /simulation passa a responder 403
```

O `GET /debts` leva cerca de 8 s com o Granite real. Use orçamento acima de R$ 3.239,10 (soma dos mínimos do sandbox): abaixo disso a recomendação sai com um aviso de orçamento insuficiente.

### 7. Testes

```bash
pytest                                                                 # unitários, sem rede
RUN_CLOUD_TESTS=1 pytest -m "integration or not integration" \
  tests/m4/test_integration.py tests/test_cloud_integration.py         # contra a cloud real, com o .env
```

Os testes de integração gravam no Cloudant compartilhado e limpam o que criam.

### 8. Problemas comuns

| Sintoma | Causa | Correção |
|---|---|---|
| `POST /consent` → `503 NOT_CONFIGURED` | `VAULT_KEK_V1` ausente | preencher a chave do time |
| `GET /debts` → `503 NOT_CONFIGURED` | flags de sandbox ausentes, ou `STORE_BACKEND=memory` | `OPEN_FINANCE_SANDBOX=true` e `SERASA_SANDBOX=true`; não usar o modo memória (o Agente 1 só fala com o Cloudant, issue #5) |
| `BXNIM0415E: Provided API key could not be found` | `IBM_CLOUD_API_KEY` inválida ou truncada (tem 44 caracteres) | gerar outra API key pessoal |
| `Authenticated user is not a member of the project` | API key do Service ID | usar API key pessoal |
| `GET /debts` → `500` logo na primeira chamada | `WATSONX_MODEL_ID=ibm/granite-3-8b-instruct`, que saiu da us-south | `ibm/granite-4-h-small` |
| log `audit: falha ao gravar destino=cos` | credencial HMAC antiga | credencial `desendividai-hmac`; o fluxo segue com o Cloudant |
| `pip install` falha no `langchain-ibm` | Python 3.14 | usar 3.11 a 3.13 |
| `/demo/` → 404 | `APP_ENV=production` | `APP_ENV=development` |

O que **não** existe nesta conta: Open Finance e Serasa reais, IBM API Connect, IBM Cloud Logs. Por isso o token do titular, o FAPI e a revogação de token no gateway estão só especificados; ver as pendências da Sub-Tarefa 1 no [`PLANO.md`](PLANO.md).

---

## Visão de produto

As seções abaixo descrevem a visão completa do produto. O escopo implementado e os desvios registrados estão no [`PLANO.md`](PLANO.md).

## Arquitetura da Solução

### Camadas Lógicas

```
┌────────────────────────────────────────────────────────────────┐
│              Camada de Engajamento e Orquestração              │
│  watsonx Assistant  ·  Agente Orquestrador  ·  watsonx.ai LLM  │
│  Agente Analista  ·  Agente Conselheiro  ·  Agente Negociador  │
└───────────────────────────┬────────────────────────────────────┘
                            │ APIs seguras (mTLS / OAuth 2.0)
┌───────────────────────────▼────────────────────────────────────┐
│             Camada de Integração e Dados                       │
│     IBM API Connect + DataPower Gateway  ·  watsonx.data       │
│         Open Finance Brasil  ·  Serasa  ·  Core BB             │
└───────────────────────────┬────────────────────────────────────┘
                            │ Metadados de decisão
┌───────────────────────────▼────────────────────────────────────┐
│           Camada de Governança e Conformidade                  │
│         watsonx.governance  ·  LGPD/Bacen  ·  Auditorias      │
└────────────────────────────────────────────────────────────────┘
```

---

## Componentes IBM

### Camada de Engajamento e Orquestração

| Componente | Função |
|---|---|
| **watsonx Assistant** | Interface conversacional nativa integrada ao App BB, WhatsApp (via Twilio) e Internet Banking. Coleta consentimento inicial e gerencia o fluxo de diálogo. |
| **watsonx.ai (Foundation Models)** | Hospeda os LLMs que alimentam os quatro agentes especializados. Suporta tool calling via REST, Python SDK e LangChain. |
| **Agente Orquestrador** | Interpreta a intenção do cliente e aciona os sub-agentes corretos via framework multiagente (ex.: AutoGen + watsonx). |
| **Agente Analista Financeiro** | Ingere dados brutos e prioriza dívidas matematicamente com base no CET. |
| **Agente Conselheiro** | Traduz jargões e cenários de simulação para linguagem empática e simples. |
| **Agente Negociador** | Avalia margens pré-aprovadas e interage via API com plataformas de acordo para buscar descontos. |

### Camada de Integração e Dados

| Componente | Função |
|---|---|
| **IBM API Connect + DataPower** | Gateway de APIs para Open Finance Brasil, Serasa e plataformas de renegociação. Gerencia autenticação mútua (mTLS), rate limiting por catálogo/gateway e criptografia de ponta a ponta. |
| **watsonx.data (Lakehouse)** | Recebe, unifica e padroniza o fluxo de dados estruturados do Open Finance, Serasa e histórico interno do BB. Formatos suportados: CSV, Parquet, JSON. Engines: Presto, Spark. |

### Camada de Governança e Conformidade

| Componente | Função |
|---|---|
| **watsonx.governance** | Monitora continuamente os modelos para ausência de viés, gera documentação de explicabilidade para cada recomendação e garante aderência à LGPD e regulações do Bacen. Suporta integração com IBM OpenPages para console de governança. |

---

## Jornada do Usuário (Fluxo de Dados)

```
1. Onboarding e Consentimento
   └── App BB → API Connect → registro explícito LGPD + Open Finance

2. Agregação (Data Ingestion)
   └── Open Finance / Serasa → API Connect → watsonx.data Lakehouse

3. Análise e Simulação
   └── watsonx.data → Agente Analista (watsonx.ai) → CET, simulações

4. Recomendação Explicável
   └── Agente Conselheiro → plano de ação neutro
   └── watsonx.governance → justificativa registrada por decisão

5. Ação e Acompanhamento
   └── Agente Negociador → API Connect → parceiros de renegociação
   └── Alertas proativos mensais via watsonx Assistant
```

---

## Papel do IBM Bob no Ciclo de Vida

| Fase | Contribuição |
|---|---|
| **Arquitetura** | Diagramas de sequência do sistema multiagente; mapeamento de fluxos de autenticação mútua (mTLS) para o ecossistema Open Finance. |
| **Código e Integração** | Geração de scripts Python/Node.js para consumo das APIs do Serasa e Open Finance via API Connect; motor de simulação (CET e amortização); testes unitários automatizados. |
| **Segurança e Conformidade** | Revisão de prompts e código-fonte contra injeção de prompt; análise das rotinas de ofuscação e anonimização de dados entre watsonx.data e watsonx.ai; aderência à LGPD. |
| **Documentação Contínua** | ADRs, esquemas OpenAPI (Swagger), mapas de fluxo de dados — mantidos em sincronia com o código produzido. |

---

## Stack de Desenvolvimento

Implementado:

| Camada | Tecnologia |
|---|---|
| API | Python 3.11–3.13, FastAPI, contrato OpenAPI 3.0 em [`openapi/openapi.yaml`](openapi/openapi.yaml) |
| Agentes | LangChain (`AgentExecutor` no Agente 1, LCEL no Agente 2) + `langchain-ibm` com Granite no watsonx.ai |
| Dados | IBM Cloudant (no lugar do MongoDB do plano), IBM Cloud Object Storage para cópia da auditoria |
| LGPD | HMAC-SHA256 para pseudônimos, AES-256-GCM com envelope encryption no cofre de identidade |

Na visão de produto (não implementado): Node.js, AutoGen, watsonx.data, IBM API Connect e watsonx.governance.

---

## Conformidade e Privacidade

- **LGPD**: Consentimento explícito coletado e registrado no API Connect antes de qualquer acesso a dados.
- **Open Finance Brasil**: Integração via API Connect respeitando os padrões do Bacen.
- **Sigilo Bancário**: Dados anonimizados no trânsito entre watsonx.data e os modelos do watsonx.ai; PII monitorada pelo watsonx.governance.
- **Auditoria**: Cada recomendação do agente tem justificativa persistida pelo watsonx.governance para auditorias regulatórias.
