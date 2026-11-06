# Infraestrutura IBM Cloud — DesendividAÍ

## Status do Ambiente (conta do hackathon)

**Conta:** `watsonx` (`63520643c32b4588b4c6f7d8546cf66e`)
**Região principal:** `us-south` (Dallas) — onde os serviços críticos estão provisionados

### Serviços disponíveis

| Serviço | Nome na conta | GUID | Região |
|---|---|---|---|
| watsonx.ai (WML) | `watsonx-Hackathon WML` | `cdb9e7c8-...` | us-south |
| Watson Studio | `watsonx-Hackathon WS` | `9338151f-...` | us-south |
| watsonx.governance | `watsonx-Hackathon GOV` | `74cba740-...` | us-south |
| watsonx Orchestrate | `watsonx-Hackathon Orchestrate` | `51d00eb6-...` | ca-tor |
| Cloudant (NoSQL) | `watsonx-Hackathon Cloudant` | `e19db8bb-...` | us-south |
| Cloud Object Storage | `watsonx-Hackathon COS` | `d0fbf605-...` | global |
| Speech to Text | `watsonx-Hackathon STT` | `7d23020d-...` | us-south |
| Text to Speech | `watsonx-Hackathon TTS` | `d7efd10d-...` | us-south |

### Limitações da conta de hackathon

A conta tem o catálogo restrito — somente os serviços pré-provisionados estão disponíveis.
Os seguintes serviços do PLANO original **não podem ser provisionados** nesta conta:

| Serviço original | Alternativa adotada | Impacto |
|---|---|---|
| IBM Cloud Databases for MongoDB | **Cloudant** (já disponível) | Cloudant é NoSQL document-store compatível; schemas do PLANO se aplicam com adaptação mínima |
| IBM Cloud Secrets Manager + HSM | **Env + KeyProvider** (`EnvKeyProvider`) | Sem HSM dedicado; chave mestra em variáveis de ambiente atrás da interface `KeyProvider`; envelope encryption com DEK por titular no cofre `identity_vault` |
| IBM Cloud Logs | **Logs JSON estruturados no stdout** (logger `desendividai.audit`) + trilha persistida em Cloudant (`audit_trail`) e COS | Sem pipeline de logs nem de alertas nesta conta (Activity Tracker também não está provisionado); alertas de acesso anômalo especificados em `docs/m4-auditoria-governanca.md` §8, mas não ativos |
| IBM API Connect | **Fora desta conta** — documentado em `openapi/openapi.yaml` para deploy futuro | OpenAPI contract pronto; gateway pode ser provisionado em conta BB de produção |

> Essas decisões são válidas **apenas para desenvolvimento e apresentação do hackathon**.
> Em produção (conta BB real), os serviços originais do PLANO devem ser usados.

---

## IAM — Service ID criado

```
Nome:        desendividai-agents
ID:          ServiceId-440c8255-c141-4aa2-92e5-ff7d034042a3
API Key:     desendividai-agents-key  (gerada, salvar no .env)
Descrição:   Service ID usado pelos Agentes 1 e 2 para acessar WML e Cloudant
```

**Como obter a API Key:** A key foi gerada durante o setup. Se precisar regenerar:

```bash
ibmcloud iam service-api-key-create desendividai-agents-key desendividai-agents \
  --description "API key para agentes DesendividAI"
```

---

## Passo a Passo: Obter credenciais faltantes (manual, via Console)

### 1. watsonx.ai — Project ID

1. Acesse [dataplatform.cloud.ibm.com](https://dataplatform.cloud.ibm.com)
2. Abra o projeto do hackathon (ou crie um novo: **New project → Create an empty project**)
3. Vá em **Manage → General** → copie o **Project ID**
4. Cole em `.env`: `WATSONX_PROJECT_ID=<valor>`

### 2. Cloudant — URL e API Key

1. Acesse [cloud.ibm.com/resources](https://cloud.ibm.com/resources)
2. Clique em **watsonx-Hackathon Cloudant**
3. Vá em **Service credentials → New credential**
4. Nome: `desendividai-cloudant-key`, Role: `Manager`
5. Copie `url` e `apikey` para o `.env`

### 3. Criar databases no Cloudant

Após ter as credenciais, obtenha primeiro um token IAM (o Cloudant usa Bearer IAM, não a API key diretamente):

```bash
# 1. Obter token IAM
IAM_TOKEN=$(curl -s -X POST https://iam.cloud.ibm.com/identity/token \
  -d "grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey=$CLOUDANT_APIKEY" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Criar os databases (M2)
# financial_snapshots — snapshots pseudonimizados do Agente 1
curl -X PUT "$CLOUDANT_URL/financial_snapshots" \
  -H "Authorization: Bearer $IAM_TOKEN"

# consent_registry — registro de consentimentos Open Finance
curl -X PUT "$CLOUDANT_URL/consent_registry" \
  -H "Authorization: Bearer $IAM_TOKEN"

# 3. Criar os databases do M4 (ou usar --ensure)
# identity_vault — cofre de identidade (CPF cifrado por titular)
curl -X PUT "$CLOUDANT_URL/identity_vault" \
  -H "Authorization: Bearer $IAM_TOKEN"

# audit_trail — append-only pela aplicação
curl -X PUT "$CLOUDANT_URL/audit_trail" \
  -H "Authorization: Bearer $IAM_TOKEN"
```

> **Alternativa para os bancos do M4:** `python -m src.privacy.retention --dry-run --ensure`
> cria `identity_vault` e `audit_trail` e aplica todos os índices Mango idempotentemente.

Ou usando o `ibmcloud` com o plugin Cloudant (se disponível):

```bash
ibmcloud resource service-key-create desendividai-cloudant-key Manager \
  --instance-name "watsonx-Hackathon Cloudant"
```

### 4. Cloud Object Storage — Credenciais HMAC

1. Acesse a instância **watsonx-Hackathon COS**
2. **Service credentials → New credential → Advanced → HMAC → On**
3. Copie `cos_hmac_keys.access_key_id` e `secret_access_key` para `.env`

> **Trilha de auditoria (M4):** os eventos são gravados no bucket `desendividai-artifacts`
> com o prefixo `audit/` (ex.: `audit/2026/11/05/<trace_id>.json`). As credenciais HMAC
> configuradas acima são as mesmas usadas pelo M4 para escrever nesse prefixo.
> Variáveis relevantes: `COS_BUCKET_NAME`, `COS_AUDIT_PREFIX=audit/`, `COS_ACCESS_KEY_ID`,
> `COS_SECRET_ACCESS_KEY`.

---

## watsonx.ai — Modelo Granite

O modelo utilizado pelo Agente 1 é `ibm/granite-4-h-small` (configurável via `WATSONX_MODEL_ID`).
O `ibm/granite-3-8b-instruct` previsto no PLANO original foi descontinuado em `us-south`.

> Para confirmar os modelos disponíveis na sua conta:
> ```bash
> curl -s "$WATSONX_URL/ml/v1/foundation_model_specs?version=2025-02-01&filters=function_text_chat" \
>   -H "Authorization: Bearer $IAM_TOKEN" | python3 -m json.tool | grep model_id
> ```

---

## Comandos úteis de verificação

```bash
# Ver todos os serviços provisionados
ibmcloud resource service-instances

# Ver Service ID dos agentes
ibmcloud iam service-id desendividai-agents

# Testar autenticação com a API Key do Service ID
ibmcloud login --apikey <API_KEY_DO_SERVICE_ID> -r us-south

# Ver target atual
ibmcloud target
```
