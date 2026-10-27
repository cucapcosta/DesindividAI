# M1 — Ciclo de Vida do Consentimento

## Base Regulatória

| Norma | Relevância |
|---|---|
| BCB Resolução Conjunta nº 3/2020 (Open Finance) | TTL máximo do consentimento: 12 meses |
| LGPD Art. 8º §5 | Consentimento revogável a qualquer tempo, sem ônus |
| LGPD Art. 18 | Direito ao esquecimento — exige deleção de dados associados |
| BCB Instrução Normativa nº 6/2021 | Ciclo de renovação e revogação de consentimentos Open Finance |

---

## Estados do Consentimento

```
                   ┌──────────────────────────────┐
                   │        AWAITING_AUTHORISATION │ ← criado por POST /consent
                   └──────────────┬───────────────┘
                                  │ Titular confirma no portal OF
                   ┌──────────────▼───────────────┐
                   │           AUTHORISED          │ ← tokens emitidos, dados acessíveis
                   └──────────────┬───────────────┘
          ┌───────────────────────┼───────────────────────┐
          │ TTL atingido          │ DELETE /consent        │ Fraude / LGPD
          │ (expiration_days)     │ (titular ou BB)        │ Art. 18
          ▼                       ▼                        ▼
  ┌──────────────┐       ┌──────────────────┐    ┌──────────────────┐
  │   EXPIRED    │       │     REVOKED      │    │     REVOKED      │
  └──────────────┘       └──────────────────┘    └──────────────────┘
          │                       │                        │
          └───────────────────────┴────────────────────────┘
                                  │
                   ┌──────────────▼───────────────┐
                   │     Tokens invalidados        │
                   │     Snapshot financeiro       │
                   │     marcado p/ deleção (M4)   │
                   └──────────────────────────────┘
```

> Estado `AWAITING_AUTHORISATION` expira em **10 minutos** sem confirmação do titular
> (TTL index no campo `awaiting_expires_at` na coleção `consent_registry`).

---

## Schema do Documento `consent_registry` (MongoDB)

```json
{
  "_id": "uuid-v4",
  "consent_id": "c7f3a2d1-...",
  "pseudonym_id": "a3f9c1...",        // HMAC-SHA256 do CPF — não armazena CPF puro
  "institutions": ["bb", "itau"],
  "scopes": ["debts:read", "simulation:read"],
  "status": "AUTHORISED",
  "created_at": "ISODate",
  "expires_at": "ISODate",            // TTL index aqui — deleção automática
  "awaiting_expires_at": "ISODate",   // TTL index — expira se não confirmado em 10min
  "revoked_at": null,
  "revocation_reason": null,          // "CUSTOMER_REQUEST" | "FRAUD" | "LGPD_ERASURE" | "EXPIRED"
  "access_token_jti": "...",          // JTI do último access token emitido
  "refresh_token_jti": "..."          // JTI do refresh token ativo
}
```

### Índices MongoDB

```javascript
// TTL — expiração automática de consentimentos
db.consent_registry.createIndex(
  { "expires_at": 1 },
  { expireAfterSeconds: 0 }
);

// TTL — expiração de consentimentos aguardando autorização (10 min)
db.consent_registry.createIndex(
  { "awaiting_expires_at": 1 },
  { expireAfterSeconds: 0,
    partialFilterExpression: { status: "AWAITING_AUTHORISATION" }
  }
);

// Busca por pseudonym_id (Agente 1 verifica consentimento ativo)
db.consent_registry.createIndex({ "pseudonym_id": 1, "status": 1 });
```

---

## TTLs Definidos

| Token / Objeto | TTL | Motivo |
|---|---|---|
| `access_token` | 15 minutos | Exposição mínima de dados financeiros |
| `refresh_token` | 90 dias (padrão) | Conforto do usuário, renovável sem re-autorização |
| Consentimento `AWAITING_AUTHORISATION` | 10 minutos | Janela de confirmação; abandono = expiração automática |
| Consentimento `AUTHORISED` | Configurável: 1–365 dias (default: 90) | BCB limita a 12 meses; BB adota 90 dias como padrão conservador |
| Snapshot financeiro (MongoDB) | 90 dias após `expires_at` do consentimento | Retenção mínima para auditoria LGPD; deleção segura por M4 |

---

## Fluxo de Renovação (Re-autorização)

Quando o consentimento expira naturalmente:

1. O canal recebe `403 Forbidden` com `code: CONSENT_EXPIRED`.
2. O canal exibe aviso ao titular: *"Seu acesso expirou. Deseja renovar?"*
3. Titular confirma → `POST /consent` cria novo consentimento.
4. Novo fluxo OAuth2 é iniciado — o titular re-autoriza no portal Open Finance.
5. O snapshot financeiro anterior permanece válido pelo período de retenção (M4).

> **Não há renovação silenciosa de consentimento.** Toda re-autorização exige confirmação
> explícita do titular (LGPD Art. 8º §1 — consentimento livre, informado e inequívoco).

---

## Fluxo de Revogação

### Por requisição do titular (`DELETE /consent/{consentId}`)

```
1. Canal chama DELETE /consent/{consentId} com Bearer token válido
2. API Connect valida token (scope: consent:write)
3. Backend:
   a. Atualiza consent_registry: status → REVOKED, revoked_at → now(), reason → "CUSTOMER_REQUEST"
   b. Chama IBM API Connect OAuth Provider: POST /oauth/revoke (revoga access + refresh tokens)
   c. Publica evento no IBM Cloud Logs para auditoria
4. Agente 1 não consegue mais buscar dados — consent check retorna REVOKED
5. Processo de deleção do snapshot financeiro agendado (ver M4 — LGPD Art. 18)
```

### Por expiração automática (TTL index MongoDB)

```
1. MongoDB TTL index deleta o documento em consent_registry
2. IBM Cloud Functions (trigger) detecta deleção → invalida refresh tokens associados
   via API interna do OAuth Provider (ou webhook configurado no API Connect)
3. IBM Cloud Logs registra evento: consent_expired
```

### Por LGPD Art. 18 (direito ao esquecimento)

```
1. Titular solicita deleção de dados (canal jurídico ou portal BB)
2. Equipe de privacidade aciona endpoint interno: POST /internal/erase/{pseudonym_id}
3. Backend:
   a. Revoga consentimento: status → REVOKED, reason → "LGPD_ERASURE"
   b. Invalida todos os tokens
   c. Encaminha pseudonym_id para M4 — deleção do snapshot financeiro + chave HSM
4. Prazo de execução: 15 dias corridos (BCB + LGPD)
```

---

## Eventos Auditáveis (IBM Cloud Logs)

| Evento | Severidade | Campos obrigatórios |
|---|---|---|
| `consent.created` | INFO | consent_id, pseudonym_id, institutions, scopes, expires_at |
| `consent.authorised` | INFO | consent_id, pseudonym_id |
| `consent.expired` | INFO | consent_id, pseudonym_id, reason |
| `consent.revoked` | WARNING | consent_id, pseudonym_id, revocation_reason, revoked_by |
| `consent.erasure_requested` | WARNING | pseudonym_id, requested_at |
| `token.refresh` | DEBUG | consent_id, jti_old, jti_new |
| `token.introspection_failed` | WARNING | endpoint, client_id, reason |
