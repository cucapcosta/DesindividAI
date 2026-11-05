# M4 — ROPA (Registro de Atividades de Tratamento — LGPD Art. 37)

Este documento é o Registro de Operações de Tratamento de Dados Pessoais (ROPA) do sistema DesindividAI, conforme exigido pelo Art. 37 da LGPD. Todos os campos marcados como **[a definir pelo controlador]** devem ser preenchidos pela equipe jurídica/DPO antes de uma eventual entrada em produção. Nomes de pessoas físicas não são incluídos aqui por deliberação de privacidade.

---

## 1. Identificação do controlador

| Atributo | Valor |
|---|---|
| Razão social | [a definir pelo controlador] |
| CNPJ | [a definir pelo controlador] |
| Endereço | [a definir pelo controlador] |
| DPO (Encarregado) | [a definir pelo controlador] |
| Contato do DPO | [a definir pelo controlador] |
| Operador principal | IBM Cloud (Cloudant, Cloud Object Storage, watsonx.ai) — ver contrato DPA |

---

## 2. Atividades de tratamento

### 2.1 Análise e priorização de dívidas financeiras (fluxo principal)

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Análise de perfil de endividamento via Open Finance e Serasa |
| **Finalidade** | Auxiliar o titular a compreender e priorizar suas dívidas; calcular CET anual por contrato; gerar retrato financeiro (snapshot) pseudonimizado |
| **Base legal (LGPD)** | Art. 7º, I — consentimento do titular, livre, informado e inequívoco |
| **Norma setorial** | BCB Resolução Conjunta nº 3/2020 (Open Finance Brasil) |
| **Categorias de titulares** | Pessoas físicas correntistas de instituições financeiras participantes do Open Finance Brasil |
| **Categorias de dados pessoais** | CPF (pseudonimizado como HMAC-SHA256); dados de dívidas e contratos de crédito (saldos, parcelas, CET, tipo de produto, instituição); faixa de score de crédito Serasa (nunca o score exato) |
| **Dados sensíveis (LGPD Art. 5º, II)** | Não há categorias especiais de dados sensíveis no tratamento atual |
| **Destinatários (compartilhamento)** | Open Finance (instituições financeiras autorizadas pelo titular); Serasa Experian (consulta de score/restrições — envio do CPF re-identificado pelo cofre **previsto quando o Serasa real for integrado**; hoje o sandbox recebe apenas o `pseudonym_id`); IBM Cloud (processamento e armazenamento sob DPA) |
| **Transferência internacional** | Dados hospedados em IBM Cloud us-south (Dallas, EUA) — requer cláusula contratual padrão ou equivalente nos termos do Art. 33 LGPD / ANPD |
| **Retenção** | Snapshot: `expires_at do consentimento + 90 dias`; Consentimento REVOKED: até `revoked_at + 90 dias`; Consentimento AWAITING: 10 minutos; cofre de identidade: até crypto-shredding |
| **Medidas de segurança** | Pseudonimização HMAC-SHA256; AES-256-GCM (DEK por titular + envelope KEK); guarda de PII antes de persistir; trilha de auditoria imutável; acesso ao CPF somente via `vault.reveal()` auditado; endpoints internos protegidos por token HMAC |

---

### 2.2 Apagamento de dados (LGPD Art. 18)

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Atendimento a pedido de apagamento do titular |
| **Finalidade** | Cumprir o direito de apagamento; remover dados do titular de todos os bancos e tornar o CPF irrecuperável |
| **Base legal** | LGPD Art. 18, VI — direito à eliminação dos dados pessoais tratados com o consentimento |
| **Titulares** | Pessoas físicas que solicitaram o apagamento |
| **Dados envolvidos** | Consentimentos, snapshots financeiros e cofre de identidade do titular |
| **Prazo de execução** | 15 dias corridos (Art. 19, II LGPD) |
| **Medidas** | Revogação de consentimentos (`LGPD_ERASURE`), deleção de snapshots, crypto-shredding do cofre; auditoria em `consent.erasure_requested` e `erasure.completed` |

---

### 2.3 Auditoria e governança

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Registro de trilha de auditoria |
| **Finalidade** | Conformidade regulatória; rastreabilidade de acesso e operações sobre dados do titular; suporte a investigações |
| **Base legal** | Art. 7º, II LGPD (cumprimento de obrigação legal); Art. 37 LGPD (registro de operações) |
| **Categorias de dados** | Apenas pseudônimos (`pseudonym_id`), identificadores de sessão (`trace_id`, `consent_id`, `snapshot_id`), tipo de evento e metadados de operação — **nunca CPF nem dados financeiros em claro** |
| **Retenção** | `audit_trail` nunca é expurgado pelo job de retenção |
| **Destinatários** | Cloudant (banco `audit_trail`) + COS (bucket `desendividai-artifacts`, prefixo `audit/`) |

---

## 3. Compartilhamentos e operadores

| Terceiro | Papel | Dados compartilhados | Instrumento |
|---|---|---|---|
| IBM Cloud (Cloudant) | Operador de armazenamento | Consentimentos (pseudonimizados), snapshots, cofre (cifrado), trilha de auditoria | DPA com IBM |
| IBM Cloud Object Storage | Operador de armazenamento | Cópias JSON da trilha de auditoria (apenas pseudônimos) | DPA com IBM |
| IBM watsonx.ai | Operador de processamento | Contexto da sessão sem PII (pseudonymized IDs, dados financeiros agregados) | DPA com IBM |
| Open Finance Brasil (instituições) | Fonte de dados / parceiro | Consentimento OAuth + dados financeiros enviados pelas instituições | Termos BCB; Resolução Conjunta 3/2020 |
| Serasa Experian | Parceiro de dados | CPF via `vault.reveal()` — **previsto quando o Serasa real for integrado**; hoje o sandbox recebe apenas o `pseudonym_id` | Contrato de fornecimento de dados [a definir pelo controlador] |

---

## 4. Direitos dos titulares (LGPD Art. 18)

| Direito | Canal de exercício | Implementação |
|---|---|---|
| Confirmação de tratamento (inciso I) | [a definir pelo controlador] | `consent_registry` registra o tratamento |
| Acesso aos dados (inciso II) | [a definir pelo controlador] | `GET /debts` retorna o snapshot atual (M2) |
| Correção (inciso III) | [a definir pelo controlador] | Novo consentimento + novo snapshot |
| Anonimização / bloqueio / eliminação (incisos IV–VI) | Equipe de privacidade → `POST /internal/erase/{pseudonym_id}` | Implementado (Parte A); prazo: 15 dias |
| Portabilidade (inciso V) | [a definir pelo controlador] | Plano / produção |
| Revogação do consentimento (inciso IX) | Portal do banco / `DELETE /consent/{id}` (M1 stub) | Revogação OAuth pelo M1; crypto-shredding pelo M4 |
| Revisão de decisões automatizadas (inciso IX) | [a definir pelo controlador] | Plano / produção (Parte B) |

---

## 5. Medidas de segurança (LGPD Art. 46)

| Medida | Estado |
|---|---|
| Pseudonimização HMAC-SHA256 (chave mestra segregada) | Implementado (Parte A) |
| Envelope encryption AES-256-GCM (DEK por titular + KEK) | Implementado (Parte A) |
| Crypto-shredding (apagamento por destruição da DEK) | Implementado (Parte A) |
| Guarda de PII em todas as camadas de persistência e auditoria | Implementado (Parte A) |
| Controle de acesso ao CPF por `purpose` auditado (`SERASA_QUERY`, `LEGAL_REQUEST`) | Implementado (Parte A) |
| Autenticação IAM em Cloudant e COS | Implementado (Parte A) |
| Endpoints internos protegidos por X-Internal-Token (HMAC) | Implementado (Parte A) |
| Gestão de chaves via Secrets Manager + HSM (Key Protect) | **Plano / produção** — `SecretsManagerKeyProvider` é esqueleto |
| Trusted Profile IAM (sem API key em texto plano) | **Plano / produção** |
| Alertas de acesso anômalo (Activity Tracker) | **Plano / produção** |

---

## 6. Histórico de revisões deste ROPA

| Versão | Data | Autor | Descrição |
|---|---|---|---|
| 1.0 | 2026-11-05 | Equipe M4 | Criação inicial — reflete Parte A do M4 |

---

## Referências no código

| Módulo | Caminho |
|---|---|
| Apagamento (Art. 18) | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
| Endpoint interno | [`src/api/routers/internal.py`](../src/api/routers/internal.py) |
| Cofre de identidade | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Retenção | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Mapeamento de dados | [`docs/m4-mapeamento-dados.md`](m4-mapeamento-dados.md) |
| Ciclo de vida do consentimento | [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md) |
