# M4 — Mapeamento de Dados Pessoais e Financeiros

Este documento cataloga todos os campos pessoais e financeiros tratados pelo sistema DesindividAI (M1 + M2 + M4), indicando origem, finalidade, onde cada dado é armazenado e qual tratamento de privacidade é aplicado. A fonte da verdade é o código; este documento reflete o estado **Implementado (Parte A)**.

---

## 1. Dados de identificação do titular

| Campo | Origem | Finalidade | Onde fica em repouso | Tratamento |
|---|---|---|---|---|
| CPF (bruto) | Entrada do titular no portal do banco | Identificar o titular; gerar pseudônimo; consultar Serasa | Nunca persiste em claro; vive apenas em memória durante `identity_vault.store()` | Cifrado com AES-256-GCM (DEK por titular) antes de persistir; chave kek removida no shred |
| CPF (normalizado) | `normalize_cpf()` em `src/privacy/pseudonymization.py` | Alimentar HMAC para gerar `pseudonym_id` | Apenas em memória; nunca gravado | Consumido imediatamente pelo HMAC; descartado |
| `pseudonym_id` | `pseudonymize_cpf()` — HMAC-SHA256 com prefixo `"cpf:"` e chave `PSEUDONYM_HMAC_KEY` | Correlacionar snapshots, consentimentos e eventos de auditoria sem expor o CPF | `consent_registry`, `financial_snapshots`, `audit_trail`, `identity_vault` (como `_id`) | Pseudonimização determinística; irreversível sem a chave mestra |
| `key_version` | `current_key_version()` em `pseudonymization.py` | Registrar qual versão da chave gerou o pseudônimo; suporte a rotação | Campo `key_version` nos documentos do `identity_vault` e nos snapshots | Metadado de controle; sem dado pessoal |

---

## 2. Cofre de identidade (`identity_vault`)

| Campo | Finalidade | Formato em repouso | Tratamento |
|---|---|---|---|
| `_id` (= `pseudonym_id`) | Chave primária do documento | String hex de 64 chars | Pseudônimo; irreversível sem a chave |
| `cpf_ciphertext` | CPF cifrado para re-identificação (Serasa, obrigações legais) | Base64 de AES-256-GCM | Cifrado com DEK aleatória por titular (AAD = `pseudonym_id`) |
| `nonce` | Nonce da cifragem do CPF | Base64 (12 bytes) | Material criptográfico; sem valor por si só |
| `wrapped_dek` | DEK envolvida pela KEK | Base64 de AES-256-GCM | Cifrado pela KEK (`VAULT_KEK_VN`); apagado no shred |
| `dek_nonce` | Nonce do envelope da DEK | Base64 (12 bytes) | Apagado no shred |
| `kek_version` | Versão da KEK que envolve a DEK | String (ex.: `"v1"`) | Metadado de controle |
| `key_version` | Versão da chave de pseudonimização | String (ex.: `"v1"`) | Metadado de controle |
| `created_at` | Data de criação do registro | ISO-8601Z | Sem dado pessoal |
| `shredded_at` | Data do crypto-shredding | ISO-8601Z ou `null` | Preenchido em `vault.shred()`; tombstone permanece |

> **Após `shred()`:** os campos `wrapped_dek`, `dek_nonce`, `cpf_ciphertext` e `nonce` são removidos. O CPF torna-se irrecuperável, inclusive em backups.

---

## 3. Consentimento (`consent_registry`)

| Campo | Origem | Finalidade | Tratamento |
|---|---|---|---|
| `_id` / `consent_id` | UUID-v4 gerado na criação | Identificar o consentimento | Sem dado pessoal |
| `pseudonym_id` | `pseudonymize_cpf(cpf)` | Associar o consentimento ao titular sem expor o CPF | Pseudônimo |
| `institutions` | Escolha do titular | Registrar quais instituições foram autorizadas | Lista de identificadores opaco |
| `scopes` | Escolha do titular | Registrar quais operações foram autorizadas | Lista de strings |
| `status` | Estado do fluxo | Controle do ciclo de vida | `AWAITING_AUTHORISATION` → `AUTHORISED` → `REVOKED` |
| `expires_at` | TTL configurado (padrão: 90 dias, máximo: 365) | Base para retenção do snapshot | Sem dado pessoal |
| `awaiting_expires_at` | 10 min após criação | Expirar consentimentos não confirmados | Controlado pelo job de retenção |
| `revoked_at` | Preenchido na revogação | Base para o prazo de 90 dias após revogação | Sem dado pessoal |
| `revocation_reason` | Motivo da revogação | Auditoria e retenção diferenciada | `CUSTOMER_REQUEST`, `FRAUD`, `LGPD_ERASURE`, `EXPIRED` |
| `access_token_jti` / `refresh_token_jti` | Emitidos pelo OAuth Provider | Invalidação de tokens | Identificadores de token; sem dado pessoal |

---

## 4. Snapshot financeiro (`financial_snapshots`)

O snapshot **nunca contém CPF, nome, número de contrato ou score exato**. Campos validados pela guarda de PII (`assert_no_pii`) antes de salvar — ver [`src/privacy/pii_guard.py`](../src/privacy/pii_guard.py).

| Campo | Finalidade | Tratamento |
|---|---|---|
| `snapshot_id` | Identificador único do snapshot | UUID-v4; sem dado pessoal |
| `pseudonym_id` | Associar o snapshot ao titular | Pseudônimo |
| `consent_id` | Rastrear qual consentimento originou o snapshot | Referência opaca |
| `generated_at` | Data/hora da geração | Sem dado pessoal |
| `expires_at` | `expires_at do consentimento + 90 dias` | Base para o job de retenção |
| `total_outstanding` | Saldo total devedor (R$) | Dado financeiro agregado; sem identificador |
| `total_monthly_payment` | Parcela mensal total (R$) | Dado financeiro agregado |
| `weighted_avg_cet` | CET médio ponderado (%) | Dado financeiro calculado |
| `debts_count` | Número de contratos | Dado financeiro agregado |
| `credit_score_band` | Faixa de score Serasa | **Faixa** (ex.: `"C"`), nunca o score exato |
| `sources.open_finance.institutions` | Instituições consultadas | Lista de identificadores (sem dados pessoais) |
| `sources.serasa.available` | Indica se o Serasa respondeu | Booleano |
| `debts[].debt_id` | Referência opaca ao contrato | `pseudonymize_contract(institution, contract_id)` — HMAC-SHA256[:32] |
| `debts[].contract_ref` | Referência interna opaca | Derivada do `debt_id`; sem número de contrato real |
| `debts[].institution` | Identificador da instituição | Código (ex.: `"bb"`); sem dado pessoal |
| `debts[].product_type` | Tipo de produto financeiro | Ex.: `"CREDIT_CARD"`, `"LOAN"` |
| `debts[].outstanding_balance` | Saldo devedor do contrato (R$) | Valor financeiro individual |
| `debts[].monthly_payment` | Parcela mensal do contrato (R$) | Valor financeiro individual |
| `debts[].cet_annual` | CET anual calculado (%) | Calculado pelo Agente 1; sem identificador |
| `debts[].is_negativado` | Flag de negativação | Booleano |
| `agent.name` | Nome do agente que gerou o snapshot | Literal `"agent1-analysis-prioritization"` |
| `agent.model_id` | Modelo LLM utilizado | Ex.: `"ibm/granite-4-h-small"` |
| `agent.tool_calls` | Sequência de ferramentas chamadas | Lista com `{tool, at, ok, duration_ms}`; sem dado pessoal |
| `key_version` | Versão da chave de pseudonimização | Metadado de controle |

---

## 5. Trilha de auditoria (`audit_trail`)

| Campo | Finalidade | Tratamento |
|---|---|---|
| `trace_id` (= `_id`) | Identificador único do evento | UUID-v4 |
| `recorded_at` | Data/hora do registro | ISO-8601Z |
| `event_type` | Tipo do evento (lista fechada no código) | Ver seção 6 |
| `pseudonym_id` | Associar o evento ao titular | Pseudônimo; nunca CPF |
| `actor` | Sistema ou papel que originou o evento | Ex.: `"agent1"`, `"retention"`, `"internal_api"` |
| Campos adicionais | Contexto do evento (ex.: `consent_id`, `snapshot_id`) | Passam pela guarda de PII antes de gravar |

> O `audit_trail` **nunca é expurgado** pelo job de retenção.

---

## 6. Tipos de evento de auditoria (lista fechada)

Definidos em [`src/privacy/audit.py`](../src/privacy/audit.py) na constante `_ALLOWED_EVENT_TYPES`:

| Evento | Quando ocorre |
|---|---|
| `consent.expired` | Consentimento `AUTHORISED` com `expires_at` < agora (job de retenção) |
| `consent.erasure_requested` | Início de `POST /internal/erase/{pseudonym_id}` |
| `erasure.completed` | Conclusão bem-sucedida do apagamento |
| `vault.reveal` | Toda chamada a `IdentityVault.reveal()`, com sucesso ou falha |
| `retention.run` | Conclusão do job de retenção (inclusive `dry_run`) |
| `agent1.snapshot_persisted` | Snapshot gravado com sucesso em `persist_snapshot()` |
| `audit.pii_blocked` | PII detectado em evento de auditoria — o evento original é substituído |

---

## 7. Dados trafegados para terceiros

| Terceiro | Dado enviado | Base legal | Controle |
|---|---|---|---|
| Open Finance (instituições autorizadas) | Apenas o consentimento — as instituições enviam dados para o sistema | Art. 7º, I LGPD; BCB Res. Conj. 3/2020 | Tokens OAuth; escopos limitados |
| Serasa | CPF do titular via `resolve_for_serasa()` — **previsto quando o Serasa real for integrado; hoje o sandbox recebe apenas o `pseudonym_id`** | Art. 7º, I LGPD; Art. 7º LGPD base legítimo interesse (análise de crédito) | Re-identificação controlada pelo cofre; auditada como `vault.reveal` |
| IBM Cloud (Cloudant, COS, watsonx.ai) | Dados pseudonimizados; CPF apenas cifrado no cofre | Contrato de processamento de dados (DPA) com IBM | IAM; dados em repouso cifrados; cofre com envelope encryption |

---

## Referências no código

| Módulo | Caminho |
|---|---|
| Pseudonimização | [`src/privacy/pseudonymization.py`](../src/privacy/pseudonymization.py) |
| Provedor de chaves | [`src/privacy/key_provider.py`](../src/privacy/key_provider.py) |
| Cofre de identidade | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Guarda de PII | [`src/privacy/pii_guard.py`](../src/privacy/pii_guard.py) |
| Auditoria | [`src/privacy/audit.py`](../src/privacy/audit.py) |
| Retenção | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Apagamento | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
| Tools do Agente 1 | [`src/agents/agent1/tools.py`](../src/agents/agent1/tools.py) |
| Schema do consentimento | [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md) |
