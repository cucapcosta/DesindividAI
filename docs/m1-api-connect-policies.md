# M1 — Políticas IBM API Connect: Auth, Rate Limiting e CORS

## Referência

IBM API Connect V10 — DataPower API Gateway (managed).
Fonte: IBM API Connect docs — `docs_ibm_api_connect_cloud` + `docs_api_connect`.

---

## 1. Native OAuth Provider

Configuração do provedor OAuth nativo no API Connect (Cloud Manager → Resources → OAuth Providers):

| Parâmetro | Valor | Justificativa |
|---|---|---|
| Type | Native | API Connect gerencia tokens internamente (DataPower) |
| Grant types | `authorization_code` | FAPI proíbe implicit; client_credentials apenas para microserviços internos do cliente |
| PKCE | Obrigatório (`S256`) | Requisito FAPI 1.0 Advanced |
| Access token TTL | 900s (15 min) | Curta duração — dados financeiros sensíveis |
| Refresh token TTL | 7.776.000s (90 dias) | Alinhado ao TTL padrão de consentimento Open Finance |
| Max consent TTL | 31.536.000s (365 dias) | Limite máximo permitido pela BCB Resolução 3/2020 |
| Token format | JWT (opaque não recomendado — requer introspection em cada chamada) | JWT permite validação local no gateway sem round-trip |
| Token revocation | Native (DataPower) | Revogação síncrona via endpoint `/oauth/revoke` |
| Introspection path | `/oauth/introspect` | RFC 7662 — expõe metadata do token para microserviços internos |
| Scopes | `openid consent:write debts:read simulation:read recommendation:read` | Princípio do menor privilégio por endpoint |

### Configuração de Tokens (`oauth_native_tokens_apim`)

```yaml
# Trecho do assembly YAML da Native OAuth Provider
token-management:
  access-token-ttl: 900
  refresh-token-ttl: 7776000
  maximum-consent-ttl: 31536000
  token-secret:
    type: hs256          # HMAC-SHA256 para assinatura do JWT interno
  refresh-token-rotation: true   # Emite novo refresh token a cada uso
```

---

## 2. Política de Autenticação por Endpoint

Todas as APIs do DesendividAÍ usam o bloco `security` abaixo no YAML de cada API:

```yaml
security:
  - openfinance_oauth2:
      - openid
      - debts:read      # (substitua pelo scope do endpoint)
```

O API Connect valida o JWT Bearer token e os scopes **antes** de rotear para o backend.
Requisições sem token ou com scope errado retornam `401 Unauthorized` ou `403 Forbidden`
diretamente no gateway, sem atingir os agentes LangChain.

---

## 3. Rate Limiting

Configurado em **Product-level** (API Connect Product YAML) para o produto `desendividai-product`:

```yaml
plans:
  default-plan:
    rate-limits:
      # Limite por chave de API (client application)
      default:
        value: 60/1minute   # 60 req/min por aplicação
        hard-limit: true
    burst-limits:
      default:
        value: 10/1second   # Burst máximo de 10 req/s

  # Plano interno para microserviços (Agente 1 → Agente 2)
  internal-plan:
    rate-limits:
      default:
        value: 600/1minute
    burst-limits:
      default:
        value: 50/1second
```

### Limites específicos por endpoint

| Endpoint | Rate limit | Justificativa |
|---|---|---|
| `POST /consent` | 5/min por CPF | Previne criação abusiva de consentimentos |
| `GET /debts` | 10/min por consent_id | Chamada pesada — coleta OF + Serasa + Agente 1 |
| `POST /simulation` | 20/min por snapshot_id | Motor de simulação Python — custo computacional médio |
| `POST /recommendation` | 10/min por simulation_id | Granite LLM — custo por token |
| `DELETE /consent` | 20/min | Operação de revogação — sem custo computacional alto |

### Resposta de Rate Limit

HTTP 429 com headers padrão:
```
Retry-After: 60
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1720000000
```

---

## 4. CORS

Configurado via policy `cors` no assembly de cada API:

```yaml
assembly:
  execute:
    - cors:
        title: CORS DesendividAÍ
        allow-origins:
          - https://app.bb.com.br
          - https://portal.bb.com.br
          # Sandbox apenas
          - https://sandbox.bb.com.br
        allow-methods:
          - GET
          - POST
          - DELETE
          - OPTIONS
        allow-headers:
          - Authorization
          - Content-Type
          - X-Request-ID
        expose-headers:
          - X-RateLimit-Limit
          - X-RateLimit-Remaining
          - Retry-After
        allow-credentials: true
        max-age: 3600
```

> **Nota:** `allow-credentials: true` é necessário porque o canal envia cookies de sessão
> do portal do cliente. Isso proíbe o uso de `allow-origins: ["*"]` — origens devem ser explícitas.

---

## 5. Headers de Segurança (Global Policy Pre-hook)

Aplicado a todas as APIs via Global Policy no catálogo:

```yaml
global-policy: 1.0.0
info:
  name: security-headers
  title: Security Headers
  version: 1.0.0
gateways:
  - datapower-api-gateway
assembly:
  execute:
    - set-variable:
        title: Security Headers
        actions:
          - set: message.headers.Strict-Transport-Security
            value: max-age=31536000; includeSubDomains
          - set: message.headers.X-Content-Type-Options
            value: nosniff
          - set: message.headers.X-Frame-Options
            value: DENY
          - set: message.headers.Cache-Control
            value: no-store
```

---

## 6. Resumo da Responsabilidade: IBM API Connect vs. Os Guri da IBM

Conforme documentação `docs_ibm_api_connect_cloud`:

| Responsabilidade | IBM API Connect (gerenciado) | Os Guri da IBM (equipe) |
|---|---|---|
| Enforcar rate limits | ✅ DataPower API Gateway | Define regras no Product YAML |
| Validar OAuth tokens | ✅ Native OAuth Provider | Configura scopes e TTLs |
| Aplicar CORS | ✅ Gateway policy | Define allowed origins |
| Provisionar gateway | ✅ IBM gerencia | Monitora uptime via IBM Cloud Logs |
| Definir APIs (OpenAPI) | — | ✅ `openapi/openapi.yaml` |
| Publicar produto | — | ✅ Via IBM API Connect Manager UI ou CLI |
