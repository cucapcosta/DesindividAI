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
                                  │ GET /consent/callback?code=... (AUTHORISED)
                                  │ ou error=... / timeout (REJECTED)
                      ┌───────────┴──────────────┐
                      ▼                          ▼
         ┌──────────────────────┐    ┌──────────────────┐
         │      AUTHORISED      │    │     REJECTED     │
         └──────────┬───────────┘    │  (ACCESS_DENIED  │
                    │                │   ou TIMEOUT)    │
         ┌──────────┴────────┐       └──────────────────┘
         │ DELETE /consent   │
         │ (CUSTOMER_REQUEST)│
         ▼                   ▼
  ┌──────────────────┐  (idempotente)
  │     REVOKED      │
  └──────────────────┘
```

**Não existe estado `EXPIRED`** no enum ou no banco. Consentimentos expirados são
recusados pelos consumidores via `expires_at` (M2 e M3 já fazem isso). A limpeza
física de documentos vencidos fica com a retenção do M4.

> Estado `AWAITING_AUTHORISATION`: o prazo de autorização (`awaiting_expires_at`)
> vence em **10 minutos**. Campo ausente ou inválido → tratado como já vencido
> (falha fechada). A limpeza física desses documentos fica com a retenção do M4.

---

## Schema do Documento `consent_registry` (Cloudant)

> **Nota:** o plano original assumia MongoDB com TTL index. O hackathon usa Cloudant,
> que não tem TTL index nativo. A expiração é **preguiçosa**: os consumidores verificam
> `expires_at` e `awaiting_expires_at` antes de usar o documento. A limpeza física
> fica com o processo de retenção do M4.

```json
{
  "_id": "<consent_id>",
  "type": "consent",
  "consent_id": "uuid-v4",
  "pseudonym_id": "hmac-hex",
  "key_version": "v1",
  "institutions": ["bb", "itau"],
  "scopes": ["debts:read", "simulation:read"],
  "status": "AWAITING_AUTHORISATION",
  "created_at": "2026-11-06T12:00:00+00:00",
  "expires_at": "2027-02-04T12:00:00+00:00",
  "awaiting_expires_at": "2026-11-06T12:10:00+00:00",
  "state_hash": "sha256-hex",
  "authorised_at": null,
  "rejected_at": null,
  "rejection_reason": null,
  "revoked_at": null,
  "revocation_reason": null,
  "revoked_by": null,
  "token_revocation": null
}
```

- **`_id` = `consent_id`** para permitir `store.get` direto.
- **Nunca** gravar o CPF, nem mascarado. O CPF fica só cifrado no cofre de identidade do M4.
- `key_version`: versão da chave que gerou o `pseudonym_id`; o Agente 1 a copia para o snapshot.
- `rejection_reason`: `"ACCESS_DENIED"` | `"AUTHORISATION_TIMEOUT"` | `"CUSTOMER_CANCELLED"` (DELETE antes de autorizar).
- `revocation_reason`: `"CUSTOMER_REQUEST"` (único valor que o M1 grava; `FRAUD` e `LGPD_ERASURE` são de outros fluxos).
- `token_revocation`: `"ok"` | `"failed"` | `"skipped"`.
- `state_hash` é removido do documento após a autorização bem-sucedida (state de uso único).

### Índices Cloudant (`scripts/cloud_setup.py`)

```python
# by-state-hash — busca no GET /consent/callback
{"index": {"fields": ["state_hash"]}, "name": "by-state-hash", "ddoc": "idx-state-hash"}

# by-consent-id — busca do M2 (get_consent) e do M3 (ensure_consent)
{"index": {"fields": ["consent_id"]}, "name": "by-consent-id", "ddoc": "idx-consent-id"}
```

Criar com `python -m scripts.cloud_setup` (idempotente — o Cloudant responde `"exists"` se já existir).

---

## TTLs Definidos

| Objeto | TTL | Motivo |
|---|---|---|
| Consentimento `AWAITING_AUTHORISATION` | 10 minutos | Janela de confirmação; campo `awaiting_expires_at` |
| Consentimento `AUTHORISED` | Configurável: 1–365 dias (default: 90) | BCB limita a 12 meses |
| Snapshot financeiro | 90 dias após `expires_at` do consentimento | Retenção LGPD; deleção física pelo M4 |

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

## Escopos do M1 no gateway

| Endpoint | Escopo |
|---|---|
| `POST /consent`, `DELETE /consent/{consentId}` | `consent:write` |
| `GET /consent/{consentId}` | `consent:read` |

---

## Fluxo de Revogação

### Por requisição do titular (`DELETE /consent/{consentId}`)

```
1. Canal chama DELETE /consent/{consentId}
2. API Connect valida token (scope: consent:write)  [pendência de configuração]
3. Backend, se AUTHORISED:
   a. Atualiza consent_registry: status → REVOKED, revoked_at, reason → "CUSTOMER_REQUEST"
      e audita consent.revoked
   b. Tenta POST OAUTH_REVOKE_URL (se configurado); falha não impede a revogação
   c. Relê o documento e grava token_revocation: "ok" | "failed" | "skipped"
      (melhor-esforço: falha aqui não vira erro para o titular)
   Se ainda AWAITING_AUTHORISATION: status → REJECTED, reason → "CUSTOMER_CANCELLED"
   (não há tokens; a retenção do M4 limpa em minutos). Já REVOKED/REJECTED: 204 sem escrita.
   As escritas são condicionais ao _rev lido: um callback concorrente recebe 409
   em vez de sobrescrever a revogação
4. Agente 1 não consegue mais buscar dados — consent check retorna REVOKED
5. Dados ficam retidos 90 dias; deleção física fica com o M4
```

**Nota — contrato da revogação de token (pendência):** o backend envia
`POST OAUTH_REVOKE_URL {consent_id}`. O contrato exato do endpoint do IBM API Connect
(parâmetros, autenticação, body format) ainda precisa ser definido pela equipe de API.
O backend não detém os tokens — eles ficam no API Connect.

### Por LGPD Art. 18 (direito ao esquecimento)

```
1. Titular solicita deleção de dados (canal jurídico ou portal BB)
2. Equipe de privacidade aciona: POST /internal/erase/{pseudonym_id}  [M4]
3. Backend (M4):
   a. Revoga consentimento: status → REVOKED, reason → "LGPD_ERASURE"
   b. Apaga snapshots e decisões e destrói o registro do cofre
   (não revoga tokens: o access token expira no TTL de 15 min; a revogação do refresh
   token no API Connect depende da pendência B3)
4. Prazo de execução: 15 dias corridos (BCB + LGPD)
```

---

## Eventos Auditáveis (trilha de auditoria do M4 — `src/privacy/audit.py`)

Todos levam `pseudonym_id` e `actor="consent_api"`. Envio melhor-esforço: falha na
auditoria não derruba a operação.

| Evento | Campos (sem CPF) |
|---|---|
| `consent.created` | consent_id, institutions, scopes, expires_at |
| `consent.authorised` | consent_id |
| `consent.rejected` | consent_id, reason (`ACCESS_DENIED`, `AUTHORISATION_TIMEOUT` ou `CUSTOMER_CANCELLED`) |
| `consent.revoked` | consent_id, revocation_reason, revoked_by (emitido antes da revogação de token; o resultado fica no campo `token_revocation` do documento) |
