# M1 — Fluxo de Consentimento OAuth2 (Open Finance Brasil)

## Padrão adotado

**FAPI 1.0 Advanced Profile** (Financial-grade API), exigido pelo Banco Central do Brasil
para Open Finance fase 2 (dados de crédito). Diferença crítica em relação ao OAuth2 padrão:

| Requisito FAPI | Detalhe |
|---|---|
| PKCE obrigatório (`S256`) | Evita interceptação de `authorization_code` |
| `response_type: code` | Apenas Authorization Code Flow (implicit proibido) |
| `request_object` JWT assinado | Parâmetros do authorization request encapsulados em JWT (PS256) |
| mTLS ou DPoP no token request | Vincula o token ao cliente que o solicitou |
| Nonce obrigatório | Anti-replay para o `id_token` |

## Simplificação em relação à API de consentimentos do Open Finance Brasil

No Open Finance Brasil (OFB) o consentimento é criado **em cada instituição transmissora**
(`POST /consents`), com `permissions` padronizadas. O DesendividAÍ simplifica isso de propósito:

| OFB | DesendividAÍ (M1) |
|---|---|
| Um consentimento por instituição transmissora | Um consentimento do titular com o BB cobrindo N instituições (`institutions`) |
| `permissions` do OFB | Escopos internos (`debts:read`, `simulation:read`, `recommendation:read`) |
| Redirect para cada transmissora | Um `redirect_url` (modo real: Authorization Server configurado) |

Quem abre os consentimentos OFB por transmissora é o **receptor Open Finance do BB**, que o
Agente 1 consulta em `/consents/{consent_id}/credit-operations` (`src/agents/agent1/sources.py`).
Mapeamento esperado dos escopos para as permissions de dados de crédito (fase 2; conferir os
nomes na versão vigente da especificação OFB):

| Escopo | Permissions OFB |
|---|---|
| `debts:read` | `RESOURCES_READ`, `LOANS_READ`, `LOANS_SCHEDULED_INSTALMENTS_READ`, `LOANS_PAYMENTS_READ`, `FINANCINGS_READ`, `FINANCINGS_SCHEDULED_INSTALMENTS_READ`, `FINANCINGS_PAYMENTS_READ`, `UNARRANGED_ACCOUNTS_OVERDRAFT_READ`, `UNARRANGED_ACCOUNTS_OVERDRAFT_PAYMENTS_READ`, `CREDIT_CARDS_ACCOUNTS_READ`, `CREDIT_CARDS_ACCOUNTS_BILLS_READ` |
| `simulation:read`, `recommendation:read` | Nenhuma — usam só o snapshot já coletado |

---

## Diagrama de Sequência

> **Especificação de produção, não o comportamento do backend.** A troca `code → token`,
> o PKCE, a introspecção e a validação de escopo acontecem no IBM API Connect. O backend do
> M1 só grava o consentimento no Cloudant e, no callback, valida o `state` (ver seção 4 e
> "Modo Sandbox").

```
Cliente (App/Canal)          API Connect (Gateway)          Auth Server (FAPI)         Agente 1 (LangChain)
        │                           │                               │                          │
        │── POST /consent ─────────▶│                               │                          │
        │   {cpf, institutions,     │── valida Bearer token ───────▶│                          │
        │    scopes, expiration}    │◀─ 200 token valid ────────────│                          │
        │                           │── backend M1: cofre + consent_registry (Cloudant)        │
        │◀─ 201 {consent_id,        │                               │                          │
        │        redirect_url,      │                               │                          │
        │        status:AWAITING}──│                               │                          │
        │                           │                               │                          │
        │── Redireciona usuário ────────────────────────────────────▶│                          │
        │   p/ redirect_url         │                               │                          │
        │                           │                               │                          │
        │         [Usuário confirma no portal Open Finance]         │                          │
        │                           │                               │                          │
        │◀─ callback com code ──────────────────────────────────────│                          │
        │   + state                 │                               │                          │
        │                           │                               │                          │
        │── POST /oauth/token ──────▶│                               │                          │
        │   {code, code_verifier,   │── valida code + PKCE ────────▶│                          │
        │    client_id, redirect_uri}│◀─ access_token + id_token ───│                          │
        │◀─ {access_token,          │                               │                          │
        │    refresh_token,         │                               │                          │
        │    expires_in: 900} ─────│                               │                          │
        │                           │                               │                          │
        │── GET /debts?consent_id── ▶│                               │                          │
        │   Authorization: Bearer   │── introspeção do token ──────▶│                          │
        │                           │◀─ {active:true, scope:...} ───│                          │
        │                           │── valida scope debts:read     │                          │
        │                           │──────────────────────────────────────────────────────────▶│
        │                           │                               │   coleta OF + Serasa      │
        │                           │                               │   calcula CET             │
        │                           │                               │   persiste snapshot       │
        │◀─ 200 {debts[], snapshot} ▶│◀─────────────────────────────────────────────────────────│
```

---

## Descrição das Etapas

### 1. Criação do Consentimento (`POST /consent`)
- O canal (app/portal) envia CPF, lista de instituições e escopos desejados.
- O API Connect valida o token do canal (`channel_oauth2`, client credentials, escopo `consent:write`). O titular ainda não tem token: ele só é emitido depois da autorização. *(pendência de configuração)*
- O servidor cifra o CPF no cofre de identidade do M4 (`identity_vault`, envelope encryption), que devolve o `pseudonym_id`; o CPF não vai para nenhum outro lugar.
- Cria o registro em `consent_registry` no Cloudant com `status: AWAITING_AUTHORISATION` e o `pseudonym_id`.
- Retorna `redirect_url` apontando para o portal Open Finance da instituição.
  - Modo sandbox (`OPEN_FINANCE_AUTH_SANDBOX=true`): a URL aponta para o próprio `GET /consent/callback?state=...&code=sandbox`.
  - Modo real: URL do Authorization Server configurado em `OPEN_FINANCE_AUTHORIZE_URL`.

### 2. Autorização pelo Titular
- O cliente é redirecionado ao portal Open Finance.
- O titular confirma o consentimento (quais dados, por quanto tempo).
- O portal emite um `authorization_code` e redireciona para o callback.

### 3. Callback (`GET /consent/callback`)
- O backend recebe `state` e `code` (ou `error`).
- Valida o `state` via `sha256(state)` contra o `state_hash` gravado no documento.
- O `state` é de **uso único**: após autorização bem-sucedida, o `state_hash` é removido
  do documento. Uma segunda chamada com o mesmo `state` retorna `404 CONSENT_NOT_FOUND`.
- Modo sandbox: `code == "sandbox"` é aceito; qualquer outro `code` → `400 INVALID_REQUEST`.
- Modo real: `code == "sandbox"` é recusado; a validade real do `code` é verificada pelo API Connect.

### 4. Troca do Código por Token — responsabilidade do API Connect
> **Limitação do backend:** a troca `code → token` (PKCE S256), a validação do PKCE e a
> emissão de `access_token` / `refresh_token` / `id_token` são responsabilidade do
> IBM API Connect Native OAuth Provider, não do backend Python. O backend apenas valida
> o `state` e persiste o novo status `AUTHORISED`.

### 5. Chamadas Autenticadas (`GET /debts`, `POST /simulation`, etc.)
- O canal inclui `Authorization: Bearer <access_token>`.
- O API Connect faz **token introspection** (RFC 7662) no Native OAuth Provider antes de rotear. *(pendência de configuração)*
- Valida scope exigido para o endpoint.
- Roteia para o Agente 1 ou Agente 2 conforme o endpoint.

### 6. Renovação do Token (Refresh)
- Quando `access_token` expira (15 min), o canal usa o `refresh_token`.
- O IBM API Connect emite novo `access_token` sem exigir nova autorização do titular.
- O `refresh_token` é rotacionado a cada uso (prevenção de replay).

---

## Modo Sandbox

Para desenvolvimento local sem Authorization Server. Com `STORE_BACKEND=memory`, o consentimento
e o cofre de identidade ficam em memória do processo; ainda são necessárias as chaves do M4
(`PSEUDONYM_HMAC_KEY` e `VAULT_KEK_V1`, ver `.env.example`).

```bash
STORE_BACKEND=memory OPEN_FINANCE_AUTH_SANDBOX=true uvicorn src.main:app --port 8000 &
R=$(curl -s -X POST localhost:8000/consent -H 'Content-Type: application/json' \
  -d '{"cpf":"52998224725","institutions":["bb","itau"],"scopes":["debts:read"]}')
echo "$R"
# redirect_url = http://localhost:8000/consent/callback?state=<...>&code=sandbox
curl -s "$(echo "$R" | python -c 'import sys,json;print(json.load(sys.stdin)["redirect_url"])')"
# → {"status":"AUTHORISED",...}
```

---

## Revogação

| Gatilho | Ação |
|---|---|
| `DELETE /consent/{consentId}` em `AUTHORISED` | (1) grava `REVOKED` e audita `consent.revoked`; (2) tenta `POST OAUTH_REVOKE_URL`; (3) grava `token_revocation` — `"ok"` / `"failed"` / `"skipped"` (melhor-esforço). Escritas condicionais ao `_rev`: um callback concorrente recebe `409 CONSENT_INVALID_STATE` |
| `DELETE /consent/{consentId}` em `AWAITING_AUTHORISATION` | Cancela: `REJECTED` com `rejection_reason=CUSTOMER_CANCELLED` (ainda não há tokens). Sai na próxima retenção do M4 |
| `expiration_days` atingido | Consumidores recusam via `expires_at` (expiração preguiçosa); o job de retenção do M4 (regra c) depois grava `REVOKED` com `revocation_reason=EXPIRED` e audita `consent.expired` |
| `AWAITING` vencido | Callback depois do prazo → `403 CONSENT_AUTHORISATION_EXPIRED`; se a retenção do M4 (regra b) já apagou o documento, `404 CONSENT_NOT_FOUND` |
| Fraude detectada | Time de segurança do BB revoga via API interna do OAuth Provider |
| Requisição LGPD Art. 18 (direito ao esquecimento) | `POST /internal/erase` (M4): consentimento → `REVOKED`/`LGPD_ERASURE`, dados apagados e cofre destruído. Não chama a revogação de token; o access token expira no TTL (15 min) e o refresh token precisa ser revogado no API Connect (mesma pendência B3) |

**Pendência B3 — contrato do endpoint de revogação de token:** o backend envia
`POST OAUTH_REVOKE_URL {"consent_id": ...}`. O contrato exato do IBM API Connect
(parâmetros, autenticação, body format) precisa ser definido pela equipe de API.
O backend não detém os tokens — eles ficam no API Connect.
