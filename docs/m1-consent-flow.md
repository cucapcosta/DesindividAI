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

---

## Diagrama de Sequência

```
Cliente (App/Canal)          API Connect (Gateway)          Auth Server (FAPI)         Agente 1 (LangChain)
        │                           │                               │                          │
        │── POST /consent ─────────▶│                               │                          │
        │   {cpf, institutions,     │── valida Bearer token ───────▶│                          │
        │    scopes, expiration}    │◀─ 200 token valid ────────────│                          │
        │                           │── persist consent_registry ──▶│(MongoDB)                 │
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
- O API Connect valida o Bearer token do canal (Service ID IAM provisionado pelo BB).
- O servidor cria um registro em `consent_registry` no MongoDB com `status: AWAITING_AUTHORISATION`.
- Retorna `redirect_url` apontando para o portal Open Finance da instituição.

### 2. Autorização pelo Titular
- O cliente é redirecionado ao portal Open Finance.
- O titular confirma o consentimento (quais dados, por quanto tempo).
- O portal emite um `authorization_code` e redireciona de volta ao callback do cliente (BB).

### 3. Troca do Código por Token (`POST /oauth/token`)
- O canal envia `code + code_verifier` (PKCE S256).
- O IBM API Connect Native OAuth Provider valida o PKCE e emite:
  - `access_token` (JWT, TTL: 15 minutos)
  - `refresh_token` (TTL: 90 dias — alinhado ao `expiration_days` do consentimento)
  - `id_token` (OIDC, confirma identidade do titular)

### 4. Chamadas Autenticadas (`GET /debts`, `POST /simulation`, etc.)
- O canal inclui `Authorization: Bearer <access_token>`.
- O API Connect faz **token introspection** (RFC 7662) no Native OAuth Provider antes de rotear.
- Valida scope exigido para o endpoint.
- Roteia para o Agente 1 ou Agente 2 conforme o endpoint.

### 5. Renovação do Token (Refresh)
- Quando `access_token` expira (15 min), o canal usa o `refresh_token`.
- O IBM API Connect emite novo `access_token` sem exigir nova autorização do titular.
- O `refresh_token` é rotacionado a cada uso (prevenção de replay).

---

## Revogação

| Gatilho | Ação |
|---|---|
| `DELETE /consent/{consentId}` (titular ou BB) | Status → `REVOKED`, refresh tokens invalidados imediatamente |
| `expiration_days` atingido | Status → `REVOKED` por TTL index no MongoDB |
| Fraude detectada (monitoramento IBM Cloud Logs) | Time de segurança do BB revoga via API interna do OAuth Provider |
| Requisição LGPD Art. 18 (direito ao esquecimento) | Revogação + deleção do snapshot financeiro |
