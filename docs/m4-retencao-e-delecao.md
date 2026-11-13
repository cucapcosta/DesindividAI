# M4 — Política de Retenção e Deleção

Este documento descreve as regras de retenção de dados, o funcionamento do job de expurgo, o fluxo de apagamento a pedido do titular (LGPD Art. 18) e a técnica de crypto-shredding adotada. A fonte da verdade é o código; este documento reflete o estado **Implementado (Parte A e Parte B)**.

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

### 1.1 Execução periódica (Implementado)

Sem execução periódica, o job não serve de nada: o Cloudant não apaga nada sozinho. Como a conta do hackathon não tem Code Engine nem outro agendador gerenciado, a própria API roda o job num laço asyncio iniciado no lifespan do router interno ([`src/privacy/retention_scheduler.py`](../src/privacy/retention_scheduler.py)).

| Variável | Padrão | Efeito |
|---|---|---|
| `RETENTION_SCHEDULE_ENABLED` | desligado | `true` liga o agendador |
| `RETENTION_INTERVAL_MINUTES` | `1440` (24 h) | Intervalo entre execuções; mínimo 5 |
| `RETENTION_INITIAL_DELAY_SECONDS` | `60` | Espera antes da primeira execução |
| `RETENTION_SCHEDULE_DRY_RUN` | desligado | `true` só calcula, não apaga |

- Cada execução grava `retention.run` na trilha, como uma execução manual.
- Execuções do mesmo processo não se sobrepõem. Com várias réplicas, cada uma roda o job; isso é seguro porque o job é idempotente.
- Falhas (Cloudant fora, por exemplo) são logadas sem PII e o laço continua.
- **Em produção:** preferir um job agendado único (ex.: Code Engine cron rodando `python -m src.privacy.retention`) e deixar `RETENTION_SCHEDULE_ENABLED` desligado.
- **Para a apresentação do hackathon:** ligar `RETENTION_SCHEDULE_ENABLED=true` no ambiente onde a API roda. Com ele desligado, a retenção depende de alguém chamar o job.

---

## 2. Regras de retenção (aplicadas na ordem)

| # | Objeto | Condição | Ação |
|---|---|---|---|
| a | Snapshot (`financial_snapshots`) | `expires_at < agora` (ou `min(expires_at, revoked_at + 90 dias)` se o consentimento vinculado estiver `REVOKED`) | Apagar o documento |
| a2 | Decisão (`decision_traces`, `type = simulation` ou `trace`) | Limite = `expires_at`; se o `consent_id` associado estiver `REVOKED` com `revoked_at`, limite = `min(expires_at, revoked_at + 90 dias)`; limite < agora → apagar. Documento sem `expires_at` válido → `skipped_invalid` | Apagar o documento (ou contar como `skipped_invalid`) |
| b | Consentimento `AWAITING_AUTHORISATION` | `awaiting_expires_at < agora` | Apagar o documento |
| c | Consentimento `AUTHORISED` | `expires_at < agora` | Marcar como `REVOKED`, `revocation_reason=EXPIRED`, `revoked_at=agora`; emitir evento `consent.expired` |
| d | Consentimento `REVOKED` | `revoked_at + 90 dias < agora` | Apagar o documento |
| e | Cofre (`identity_vault`) | Doc ativo (`shredded_at null`) com `created_at` há mais de 24 h, sem consentimento nem snapshot com o mesmo `pseudonym_id` | `IdentityVault.shred()` (crypto-shredding) |
| f | Trilha de auditoria (`audit_trail`) | Só com `AUDIT_RETENTION_DAYS` > 0: `recorded_at` mais antigo que o prazo | Apagar o evento do Cloudant. **Sem a variável (padrão), nunca expurgada** |

> **Carência de 24 h (regra e):** o cofre só é destruído automaticamente após 24 horas da criação, para evitar race conditions no provisionamento inicial do titular.

> **Regra a2 — detalhamento:** o limite efetivo de um documento de decisão é `min(expires_at, revoked_at + RETENTION_AFTER_REVOCATION_DAYS)` quando o consentimento associado estiver `REVOKED` com `revoked_at` preenchido. Documentos sem `expires_at` válido (ex.: documentos legados sem data) são contados como `skipped_invalid` e ignorados pelo job, sem geração de erro.

> **`RETENTION_AFTER_REVOCATION_DAYS`:** variável de ambiente; padrão = 90 dias (conforme `docs/m1-consent-lifecycle.md`).

> **Idempotência:** segunda execução com o mesmo `now` → todas as contagens = 0. O job verifica as condições antes de agir.

> **`dry_run`:** o modo de simulação percorre todos os documentos, calcula as contagens **identicamente** ao modo real, mas **não escreve nada**. Ao final, emite o evento `retention.run` com `dry_run=true`.

> **`RetentionReport`:** inclui o campo `decisions_deleted: int` (contagem de documentos apagados pela regra a2) além dos campos das regras a–e.

---

> **Prazo da auditoria (regra f):** a trilha guarda o `pseudonym_id`, e enquanto a chave HMAC existir quem tem o CPF recalcula o pseudônimo. Por isso a trilha continua sendo dado pessoal pseudonimizado mesmo depois do apagamento do titular, e não pode ficar guardada sem prazo. O prazo é decisão do controlador (ver [`m4-ropa.md`](m4-ropa.md) §2.4); o código já aplica qualquer valor configurado. As cópias no COS não são apagadas pela aplicação: configurar no bucket uma regra de ciclo de vida (expiração) com o mesmo prazo, compatível com o Object Lock, se houver.

## 3. Crypto-shredding

O **crypto-shredding** é a técnica usada para tornar o CPF irrecuperável pela aplicação após a deleção.

**Como funciona:**

1. Cada titular tem uma **DEK (Data Encryption Key)** aleatória de 32 bytes, gerada na criação do cofre.
2. O CPF é cifrado com AES-256-GCM usando a DEK (AAD = `pseudonym_id`).
3. A DEK é cifrada (envelope) pela **KEK (Key Encryption Key)** global (`VAULT_KEK_VN`).
4. No `shred(pseudonym_id)`:
   - Os campos `wrapped_dek`, `dek_nonce`, `cpf_ciphertext` e `nonce` são **removidos** do documento corrente com um put.
   - `shredded_at` é preenchido com a data/hora UTC.
   - O **tombstone** permanece no banco como evidência da existência e deleção do registro.
5. O CPF torna-se irrecuperável pela aplicação (o documento corrente não tem mais o material).
   Cópias anteriores do documento (revisões do Cloudant até a compactação, backups e réplicas)
   permanecem decifráveis enquanto a KEK global existir.
   Para irrecuperabilidade inclusive em backups (produção): DEK por titular em Key Protect/HSM,
   destruída no shred.

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

O apagamento é acionado pela equipe de privacidade. O caminho normal é pelo **CPF**, que é o que o titular informa no pedido:

```
POST /internal/erase
X-Internal-Token: ***
Content-Type: application/json

{"cpf": "529.982.247-25"}
```

- O CPF vai **só no corpo**, nunca na URL. É aceito com ou sem máscara e precisa ter dígitos verificadores válidos; caso contrário, `422 INVALID_CPF`, sem ecoar o valor recebido.
- O servidor calcula o `pseudonym_id` do CPF em **todas as versões** da chave de pseudonimização (`pseudonymize_cpf_all_versions`) e roda o fluxo abaixo para cada um. Sem isso, depois de uma rotação de chave o apagamento cobriria só a versão ativa e deixaria para trás os dados emitidos com as anteriores.
- Se alguma versão de chave não puder ser lida → `503 PSEUDONYM_KEY_UNAVAILABLE` e **nada** é apagado.
- Resposta: `{"subjects": [{"key_version", "pseudonym_id", "consents_revoked", "snapshots_deleted", "decisions_deleted", "vault_shredded", "trace_id", "completion_audited"}], "totals": {...}}`. Nunca contém o CPF.
- Se a auditoria inicial de um dos pseudônimos falhar → `503 AUDIT_UNAVAILABLE`. Os pseudônimos anteriores já foram apagados; como o fluxo é idempotente, repetir o pedido completa o apagamento.

Também existe a rota por um único pseudônimo, para quando a equipe já tem o `pseudonym_id`:

```
POST /internal/erase/{pseudonym_id}
X-Internal-Token: ***
```

O `pseudonym_id` deve ter exatamente 64 caracteres hexadecimais. CPFs enviados na URL são rejeitados com HTTP 422. `INTERNAL_API_TOKEN` não configurado no servidor → 503; header `X-Internal-Token` ausente ou inválido → 401 (e a linha `internal.auth_failed` no stdout, ver [`m4-auditoria-governanca.md`](m4-auditoria-governanca.md) §5.4).

```mermaid
sequenceDiagram
    actor Equipe as Equipe de Privacidade
    participant API as POST /internal/erase
    participant Consent as consent_registry
    participant Snap as financial_snapshots
    participant Dec as decision_traces
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

    API->>Dec: iter_decisions({pseudonym_id}) via índice by-pseudonym
    loop Para cada decisão do titular
        API->>Dec: delete_decision(doc)
    end
    Note right of Dec: decisions_deleted contabilizado

    API->>Vault: shred(pseudonym_id)
    Note right of Vault: Remove wrapped_dek, dek_nonce,\ncpf_ciphertext, nonce;\npreenche shredded_at

    API->>Audit: record_event("erasure.completed",\nconsents_revoked, snapshots_deleted,\ndecisions_deleted, vault_shredded, request_trace_id)
    API-->>Equipe: 200 {consents_revoked, snapshots_deleted,\ndecisions_deleted, vault_shredded, trace_id}
```

### 4.1 Idempotência

Segunda chamada ao mesmo `pseudonym_id`:

- `consents_revoked = 0` (já estão `REVOKED`).
- `snapshots_deleted = 0` (já foram apagados).
- `decisions_deleted = 0` (já foram apagadas).
- `vault_shredded = false` (DEK já não existe; `shred()` retorna `False`).

O evento `erasure.completed` ainda é registrado com contagens zero, para rastreabilidade.

### 4.2 Acesso e portabilidade (LGPD Art. 18 II, V e VII)

```
POST /internal/subject/export
X-Internal-Token: ***

{"cpf": "..."}            ← todas as versões da chave
{"pseudonym_id": "<hex>"} ← um pseudônimo
```

Devolve, por pseudônimo: consentimentos, snapshots, decisões (simulações e recomendações, com o texto gerado pelo modelo, que serve de explicação da decisão automatizada), metadados do cofre (existe, `created_at`, `key_version`, `shredded_at`; **nunca** o CPF cifrado nem a DEK), histórico de auditoria e um resumo de compartilhamentos (instituições Open Finance autorizadas, número de consultas ao Serasa, operadores). O resultado é JSON, legível por máquina (portabilidade).

Cada exportação grava `subject.access_exported` (só contagens) **antes** de devolver os dados; sem trilha, `503 AUDIT_UNAVAILABLE` e nada é devolvido. Código: [`src/privacy/subject_access.py`](../src/privacy/subject_access.py).

### 4.3 O que NÃO é feito neste endpoint

- **Revogação do token OAuth**: responsabilidade do M1 (`DELETE /consent/{id}` → IBM API Connect). O endpoint `/internal/erase` revoga o *consentimento* no banco, mas não invalida tokens OAuth diretamente.

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
| `decision_traces` | `by-pseudonym` (`[pseudonym_id]`) e `by-type-expires` (`[type, expires_at]`) — criados na Parte B |
| `identity_vault` | Sem índice dedicado — busca por `{shredded_at: null}` (varredura) |
| `audit_trail` | `by-pseudonym-recorded` (`[pseudonym_id, recorded_at]`), `by-event-recorded` (`[event_type, recorded_at]`), `by-recorded` (`[recorded_at]`, regra f) |

Os índices são criados/verificados por `ensure_indexes()` em [`src/privacy/retention_store.py`](../src/privacy/retention_store.py), idempotente. O banco `decision_traces` é criado automaticamente por `ensure_databases()` se não existir.

> **Retry em `ensure_indexes` e `ensure_databases`:** operações de criação de índice/banco que retornam HTTP 429 (Cloudant Lite rate limit) são re-tentadas com backoff exponencial (`_with_429_retry`, até 5 tentativas, máximo de 8 segundos entre tentativas). O mesmo retry protege operações de escrita e leitura do `CloudantRetentionStore`.

---

## Referências no código

| Módulo | Caminho |
|---|---|
| Job de retenção | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Store de retenção (repositórios) | [`src/privacy/retention_store.py`](../src/privacy/retention_store.py) |
| Apagamento (por pseudônimo e por CPF) | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
| Exportação do titular | [`src/privacy/subject_access.py`](../src/privacy/subject_access.py) |
| Agendador do job | [`src/privacy/retention_scheduler.py`](../src/privacy/retention_scheduler.py) |
| Cofre (crypto-shredding) | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Endpoints internos | [`src/api/routers/internal.py`](../src/api/routers/internal.py) |
| Ciclo de vida do consentimento | [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md) |
