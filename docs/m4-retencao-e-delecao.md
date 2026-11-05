# M4 — Política de Retenção e Deleção

Este documento descreve as regras de retenção de dados, o funcionamento do job de expurgo, o fluxo de apagamento a pedido do titular (LGPD Art. 18) e a técnica de crypto-shredding adotada. A fonte da verdade é o código; este documento reflete o estado **Implementado (Parte A)**.

---

## 1. Por que um job de expurgo?

O **Cloudant não suporta TTL (Time-To-Live) por documento**, diferentemente do MongoDB (usado no documento original do M1). A política de retenção descrita em [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md) — baseada em índices TTL do MongoDB — foi substituída por um **job de expurgo idempotente** implementado em [`src/privacy/retention.py`](../src/privacy/retention.py).

O job usa índices Mango criados via `ensure_indexes()` e pode ser executado de duas formas:

```bash
# CLI (verificar o que seria feito — modo seguro padrão):
python -m src.privacy.retention --dry-run

# CLI (aplicar efetivamente):
python -m src.privacy.retention

# API interna (dry_run=true é o padrão):
POST /internal/retention/run?dry_run=true
POST /internal/retention/run?dry_run=false
```

---

## 2. Regras de retenção (aplicadas na ordem)

| # | Objeto | Condição | Ação |
|---|---|---|---|
| a | Snapshot (`financial_snapshots`) | `expires_at < agora` (ou `min(expires_at, revoked_at + 90 dias)` se o consentimento vinculado estiver `REVOKED`) | Apagar o documento |
| b | Consentimento `AWAITING_AUTHORISATION` | `awaiting_expires_at < agora` | Apagar o documento |
| c | Consentimento `AUTHORISED` | `expires_at < agora` | Marcar como `REVOKED`, `revocation_reason=EXPIRED`, `revoked_at=agora`; emitir evento `consent.expired` |
| d | Consentimento `REVOKED` | `revoked_at + 90 dias < agora` | Apagar o documento |
| e | Cofre (`identity_vault`) | Doc ativo (`shredded_at null`) com `created_at` há mais de 24 h, sem consentimento nem snapshot com o mesmo `pseudonym_id` | `IdentityVault.shred()` (crypto-shredding) |
| f | Trilha de auditoria (`audit_trail`) | — | **Nunca expurgada** |

> **Carência de 24 h (regra e):** o cofre só é destruído automaticamente após 24 horas da criação, para evitar race conditions no provisionamento inicial do titular.

> **`RETENTION_AFTER_REVOCATION_DAYS`:** variável de ambiente; padrão = 90 dias (conforme `docs/m1-consent-lifecycle.md`).

> **Idempotência:** segunda execução com o mesmo `now` → todas as contagens = 0. O job verifica as condições antes de agir.

> **`dry_run`:** o modo de simulação percorre todos os documentos, calcula as contagens **identicamente** ao modo real, mas **não escreve nada**. Ao final, emite o evento `retention.run` com `dry_run=true`.

---

## 3. Crypto-shredding

O **crypto-shredding** é a técnica usada para tornar irrecuperável o CPF de um titular, mesmo em backups ou snapshots de banco criados antes da deleção.

**Como funciona:**

1. Cada titular tem uma **DEK (Data Encryption Key)** aleatória de 32 bytes, gerada na criação do cofre.
2. O CPF é cifrado com AES-256-GCM usando a DEK (AAD = `pseudonym_id`).
3. A DEK é cifrada (envelope) pela **KEK (Key Encryption Key)** global (`VAULT_KEK_VN`).
4. No `shred(pseudonym_id)`:
   - Os campos `wrapped_dek`, `dek_nonce`, `cpf_ciphertext` e `nonce` são **removidos** do documento.
   - `shredded_at` é preenchido com a data/hora UTC.
   - O **tombstone** permanece no banco como evidência da existência e deleção do registro.
5. Sem a DEK, o `cpf_ciphertext` é irreversível — mesmo que alguém acesse um backup anterior, não consegue decifrar.

```
Antes do shred:                    Após o shred:
{                                  {
  "_id": "<pseudonym_id>",           "_id": "<pseudonym_id>",
  "wrapped_dek": "...",              "type": "identity",
  "dek_nonce": "...",                "key_version": "v1",
  "cpf_ciphertext": "...",           "kek_version": "v1",
  "nonce": "...",                    "created_at": "...",
  "shredded_at": null                "shredded_at": "2026-11-05T18:00:00Z"
}                                  }
```

---

## 4. Fluxo de apagamento a pedido do titular (LGPD Art. 18)

O apagamento é acionado pela equipe de privacidade via:

```
POST /internal/erase/{pseudonym_id}
X-Internal-Token: ***
```

O `pseudonym_id` deve ter exatamente 64 caracteres hexadecimais. CPFs enviados na URL são rejeitados com HTTP 422. `INTERNAL_API_TOKEN` não configurado no servidor → 503; header `X-Internal-Token` ausente ou inválido → 401.

```mermaid
sequenceDiagram
    actor Equipe as Equipe de Privacidade
    participant API as POST /internal/erase
    participant Consent as consent_registry
    participant Snap as financial_snapshots
    participant Vault as identity_vault
    participant Audit as audit_trail + COS

    Equipe->>API: POST /internal/erase/{pseudonym_id}\nX-Internal-Token: ***
    API->>Audit: record_event("consent.erasure_requested")
    Note right of Audit: trace_id gerado (UUID4)

    API->>Consent: iter_consents({pseudonym_id})
    loop Para cada consentimento não-REVOKED
        API->>Consent: update → status=REVOKED,\nrevocation_reason=LGPD_ERASURE
    end

    API->>Snap: iter_snapshots({pseudonym_id})
    loop Para cada snapshot do titular
        API->>Snap: delete_snapshot(doc)
    end

    API->>Vault: shred(pseudonym_id)
    Note right of Vault: Remove wrapped_dek, dek_nonce,\ncpf_ciphertext, nonce;\npreenche shredded_at

    API->>Audit: record_event("erasure.completed",\nconsents_revoked, snapshots_deleted,\nvault_shredded, request_trace_id)
    API-->>Equipe: 200 {consents_revoked, snapshots_deleted,\nvault_shredded, trace_id}
```

### 4.1 Idempotência

Segunda chamada ao mesmo `pseudonym_id`:

- `consents_revoked = 0` (já estão `REVOKED`).
- `snapshots_deleted = 0` (já foram apagados).
- `vault_shredded = false` (DEK já não existe; `shred()` retorna `False`).

O evento `erasure.completed` ainda é registrado com contagens zero, para rastreabilidade.

### 4.2 O que NÃO é feito neste endpoint

- **Revogação do token OAuth**: responsabilidade do M1 (`DELETE /consent/{id}` → IBM API Connect). O endpoint `/internal/erase` revoga o *consentimento* no banco, mas não invalida tokens OAuth diretamente.
- **Deleção de dados do M3** (decisões, simulações): será tratado na Parte B, após a integração com o M3.

---

## 5. Prazo de atendimento (LGPD Art. 19, II)

> **15 dias corridos** a partir da solicitação do titular.

O prazo está previsto no Art. 19, II da LGPD e referenciado em [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md). O sistema **não gerencia o prazo automaticamente** — cabe à equipe de privacidade monitorar e acionar o endpoint dentro do prazo.

---

## 6. Bancos e índices Mango criados pelo M4

| Banco | Índices relevantes |
|---|---|
| `consent_registry` | `by-status-awaiting-expires` (`[status, awaiting_expires_at]`), `by-expires-at` (`[expires_at]`), `by-pseudonym-status` (`[pseudonym_id, status]`) |
| `financial_snapshots` | `by-pseudonym` (M2, campos: `[pseudonym_id, generated_at]`), `by-expiry` (M2, campo: `[expires_at]`) |
| `identity_vault` | Sem índice dedicado — busca por `{shredded_at: null}` (varredura) |
| `audit_trail` | `by-pseudonym-recorded` (`[pseudonym_id, recorded_at]`), `by-event-recorded` (`[event_type, recorded_at]`) |

Os índices são criados/verificados por `ensure_indexes()` em [`src/privacy/retention_store.py`](../src/privacy/retention_store.py), idempotente.

---

## Referências no código

| Módulo | Caminho |
|---|---|
| Job de retenção | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Store de retenção (repositórios) | [`src/privacy/retention_store.py`](../src/privacy/retention_store.py) |
| Apagamento | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
| Cofre (crypto-shredding) | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Endpoints internos | [`src/api/routers/internal.py`](../src/api/routers/internal.py) |
| Ciclo de vida do consentimento | [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md) |
