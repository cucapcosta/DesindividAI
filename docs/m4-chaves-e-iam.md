# M4 — Gestão de Chaves e IAM

Este documento descreve a arquitetura de gestão de chaves criptográficas, o modelo de acesso IAM e o procedimento de rotação adotados pelo M4. Distingue explicitamente o que está **Implementado (Parte A)** do que é **Plano / produção**. A fonte da verdade é o código.

---

## 1. Visão geral das chaves

O sistema usa duas famílias de chaves, gerenciadas pela interface [`KeyProvider`](../src/privacy/key_provider.py):

| Família | Identificador de propósito | Uso | Variável de ambiente (hackathon) |
|---|---|---|---|
| Chave de pseudonimização | `"pseudonym"` | HMAC-SHA256 para gerar `pseudonym_id` | `PSEUDONYM_HMAC_KEY` (v1) / `PSEUDONYM_HMAC_KEY_VN` (vN ≥ 2) |
| KEK (Key Encryption Key) do cofre | `"vault_kek"` | Envelope da DEK por titular (AES-256-GCM) | `VAULT_KEK_VN` |

> **Requisito mínimo:** hex válido com no mínimo 32 bytes (256 bits). Chaves menores são rejeitadas por `EnvKeyProvider`.

---

## 2. Hackathon vs. produção

### 2.1 Hackathon — Implementado (Parte A)

```
.env
  PSEUDONYM_HMAC_KEY=<hex ≥ 32 bytes>
  PSEUDONYM_KEY_ACTIVE_VERSION=v1

  VAULT_KEK_V1=<hex ≥ 32 bytes>
  VAULT_KEK_ACTIVE_VERSION=v1
        │
        ▼
  EnvKeyProvider.get(purpose, version=None)
        │
        ├─ purpose="pseudonym", v1 → lê PSEUDONYM_HMAC_KEY
        ├─ purpose="pseudonym", vN → lê PSEUDONYM_HMAC_KEY_VN
        └─ purpose="vault_kek", vN → lê VAULT_KEK_VN
```

- Em `APP_ENV != production`, se `PSEUDONYM_HMAC_KEY` estiver ausente, uma **chave efêmera** de 32 bytes é gerada por processo com aviso de log. Pseudônimos **não serão estáveis** entre reinícios nesse caso.
- Em `APP_ENV=production`, chave ausente é **erro fatal**.

### 2.2 Produção — Plano (não implementado)

A classe `SecretsManagerKeyProvider` em [`src/privacy/key_provider.py`](../src/privacy/key_provider.py) é um **esqueleto**. O fluxo previsto:

1. Autenticação via **Trusted Profile IAM** (sem API key em texto plano).
2. Obtenção do segredo pelo nome `desendividai/<purpose>/<version>` usando `ibm-platform-services` (`SecretsManagerV2`).
3. **Cache curto (< 5 min)** com invalidação por versão para suportar rotação sem reinício.
4. Rotação: incrementar `PSEUDONYM_KEY_ACTIVE_VERSION` ou `VAULT_KEK_ACTIVE_VERSION` → provider carrega a nova versão na próxima chamada após expiração do cache.
5. O **HSM (Key Protect)** garante que a chave nunca sai do perímetro seguro em texto plano.
6. **Nunca armazenar chaves no COS** — a menção a "COS + envelope encryption" em documentos anteriores ao M4 está desatualizada.

---

## 3. Quem acessa qual chave, com qual role

| Componente / Actor | Propósito do acesso | Chave acessada | Role IAM (produção) | Observação |
|---|---|---|---|---|
| `pseudonymize_cpf()` / `pseudonymize_contract()` | Gerar `pseudonym_id` e `debt_id` | `pseudonym` (versão ativa) | `SecretsReader` (grupo do projeto) | Chamado no M1 (criação do consentimento) e M2 (`pseudonymize_contract` em `src/agents/agent1/analysis.py`) |
| `IdentityVault.store()` | Cifrar CPF novo do titular | `pseudonym` + `vault_kek` (ativas) | `SecretsReader` | Chamado no seed e no provisionamento do titular |
| `IdentityVault.reveal()` | Decifrar CPF para re-identificação | `vault_kek` (versão registrada no doc) | `SecretsReader` | Sempre auditado; `purpose` restrito a `SERASA_QUERY` ou `LEGAL_REQUEST` |
| `IdentityVault.resolve_for_serasa()` | Re-identificar titular para o Serasa real | `vault_kek` (via `reveal()`) | `SecretsReader` | **Implementado (Parte B):** chamado pelo `HttpSerasaClient` real via `resolver` injetável; CPF apenas no corpo do POST |
| `IdentityVault.shred()` | Destruir material criptográfico | **Nenhuma chave (shred só remove campos)** | Nenhuma chave necessária | Não precisa acessar a KEK |
| Job de retenção (`retention.py`) | Executar regras de expiração | **Nenhuma chave (shred só remove campos)** na regra e; `vault_kek` indireta apenas se `reveal` for chamado | `SecretsReader` | Apenas na regra e (shred automático) |
| `POST /internal/erase` | Apagamento LGPD Art. 18 | **Nenhuma chave (shred só remove campos)** | `SecretsReader` | Autenticado por `X-Internal-Token`; cobre `decision_traces` (Parte B) |
| Testes unitários | Verificar comportamento | Chaves fixas de teste em `tests/conftest.py` | Nenhuma (sem rede) | Nunca usam a chave real do `.env` |

---

## 4. Rotação de chaves

### 4.1 Chave de pseudonimização

A rotação **não afeta pseudônimos já emitidos**, pois o campo `key_version` nos documentos preserva a versão que gerou cada pseudônimo.

**Procedimento:**

1. Gerar nova chave: `python -c "import secrets; print(secrets.token_hex(32))"`.
2. Adicionar ao `.env`: `PSEUDONYM_HMAC_KEY_V2=<nova_chave>`.
3. Atualizar: `PSEUDONYM_KEY_ACTIVE_VERSION=v2`.
4. Novos titulares serão pseudonimizados com v2; um novo `store()` do **mesmo CPF** com a chave v2 gera um `pseudonym_id` diferente (v2). Documentos emitidos com v1 continuam ligados ao pseudônimo v1 — **não são migrados automaticamente**; migração de titulares exige um job dedicado (plano).
5. Titulares existentes (v1) continuam funcionando; `reveal()` usa `kek_version` do documento para escolher a KEK correta (não `key_version` — cada campo tem seu propósito de controle separado).

> **Atenção:** pseudônimos são determinísticos por chave. Se um titular for recriado com uma nova chave, o `pseudonym_id` muda. Documentos antigos (v1) não serão vinculados automaticamente.

### 4.2 KEK do cofre

A rotação da KEK requer re-wrapping das DEKs existentes (opcional) ou manutenção de ambas as versões ativas.

**Procedimento:**

1. Gerar nova KEK: `python -c "import secrets; print(secrets.token_hex(32))"`.
2. Adicionar ao `.env`: `VAULT_KEK_V2=<nova_kek>`.
3. Atualizar: `VAULT_KEK_ACTIVE_VERSION=v2`.
4. Novos documentos no cofre serão envolvidos com v2.
5. Documentos existentes (v1) continuam legíveis — `reveal()` lê `kek_version` do documento e usa a KEK correspondente.
6. *(Opcional, produção)* Executar job de re-wrapping que lê cada doc com v1, **desembrulha a DEK** e re-embrulha com v2.

> **Sem downtime:** durante a transição, ambas as KEKs devem estar disponíveis no provider.

---

## 5. Re-identificação para o Serasa — Implementado (Parte B)

> **Estado atual:** `HttpSerasaClient.fetch_report(pseudonym_id)` em
> [`src/agents/agent1/sources.py`](../src/agents/agent1/sources.py) recebe um `resolver`
> injetável. No cliente **real**, o `resolver` padrão é `get_identity_vault().resolve_for_serasa`,
> que resolve o CPF via cofre e o envia **somente no corpo** do `POST /reports`.
> O sandbox continua usando apenas o `pseudonym_id` (sem re-identificação).

O fluxo implementado:

```
get_serasa_data(ctx, pseudonym_id)
        │
        ▼
HttpSerasaClient.fetch_report(pseudonym_id)
        │
        ▼  resolver(pseudonym_id)  ← injetado; padrão: get_identity_vault().resolve_for_serasa
IdentityVault.resolve_for_serasa(pseudonym_id)
   = reveal(purpose="SERASA_QUERY", actor="agent1")
        │
        ├─ Busca doc no identity_vault
        ├─ Desembrulha DEK com KEK
        ├─ Decifra CPF com DEK
        ├─ Registra vault.reveal (ok=true/false) em audit_trail + COS
        └─ Retorna CPF em memória
                │
                ▼
        POST /reports  ← CPF no corpo JSON apenas; nunca em URL, header ou log
```

O CPF **nunca sai do escopo de `reveal()`** em direção a outras camadas. O Agente 1 nunca vê o CPF. Erros (titular sem cofre, cofre destruído pós-shred) levantam erro claro sem vazar dados pessoais.

> **Sandbox inalterado:** o `SandboxSerasaClient` continua usando apenas o `pseudonym_id`; o `resolver` não é chamado nesse caminho.

---

## 6. Segredos proibidos

| Local | Regra |
|---|---|
| Código-fonte | Proibido hardcodear qualquer chave, token ou segredo |
| Logs / auditoria | Proibido logar chaves, CPF ou tokens OAuth |
| COS | Proibido armazenar chaves criptográficas (apenas trilha de auditoria) |
| Documentação | Proibido incluir valores de chave, CPF válido ou CPF real (use `***`) |
| Git | Proibido commitar `.env` com valores reais |

---

## 7. Variáveis de ambiente relevantes (`.env.example`)

| Variável | Descrição | Requisito |
|---|---|---|
| `PSEUDONYM_HMAC_KEY` | Chave de pseudonimização v1 | Hex ≥ 32 bytes |
| `PSEUDONYM_KEY_ACTIVE_VERSION` | Versão ativa da chave de pseudonimização | Ex.: `v1` |
| `VAULT_KEK_V1` | KEK do cofre v1 | Hex ≥ 32 bytes |
| `VAULT_KEK_ACTIVE_VERSION` | Versão ativa da KEK | Ex.: `v1` |
| `INTERNAL_API_TOKEN` | Token dos endpoints `/internal/*` | Gerado com `secrets.token_hex(32)` |
| `CLOUDANT_DB_IDENTITY_VAULT` | Nome do banco do cofre | Padrão: `identity_vault` |
| `CLOUDANT_DB_AUDIT` | Nome do banco de auditoria | Padrão: `audit_trail` |

---

## Referências no código

| Módulo | Caminho |
|---|---|
| Interface e implementações do KeyProvider | [`src/privacy/key_provider.py`](../src/privacy/key_provider.py) |
| Pseudonimização (uso da chave) | [`src/privacy/pseudonymization.py`](../src/privacy/pseudonymization.py) |
| Cofre (uso da KEK) | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Fonte Serasa (ponto de re-identificação) | [`src/agents/agent1/sources.py`](../src/agents/agent1/sources.py) |
| Endpoint interno (autenticação por token) | [`src/api/routers/internal.py`](../src/api/routers/internal.py) |
