# M4 — ROPA (Registro de Atividades de Tratamento — LGPD Art. 37)

Este documento é o Registro de Operações de Tratamento de Dados Pessoais (ROPA) do sistema DesindividAI, conforme exigido pelo Art. 37 da LGPD. Todos os campos marcados como **[a definir pelo controlador]** devem ser preenchidos pela equipe jurídica/DPO antes de uma eventual entrada em produção. Nomes de pessoas físicas não são incluídos aqui por deliberação de privacidade. Este documento reflete o estado **Implementado (Parte A e Parte B)**.

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
| **Destinatários (compartilhamento)** | Open Finance (instituições financeiras autorizadas pelo titular); Serasa Experian (consulta de score/restrições — **Implementado (Parte B):** `HttpSerasaClient` real envia o CPF resolvido pelo cofre somente no corpo do POST, nunca em URL ou log; sandbox continua enviando apenas `pseudonym_id`); IBM Cloud (processamento e armazenamento sob DPA) |
| **Transferência internacional** | Dados hospedados em IBM Cloud us-south (Dallas, EUA) — requer cláusula contratual padrão ou equivalente nos termos do Art. 33 LGPD / ANPD |
| **Retenção** | Snapshot: `expires_at` herdado do snapshot (ou `now + DECISION_RETENTION_DAYS` para snapshots legados); Decisões (`decision_traces`): `expires_at` do documento, antecipado para `revoked_at + 90 dias` se o consentimento for revogado (regra a2); Consentimento REVOKED: até `revoked_at + 90 dias`; Consentimento AWAITING: 10 minutos; cofre de identidade: até crypto-shredding |
| **Medidas de segurança** | Pseudonimização HMAC-SHA256; AES-256-GCM (DEK por titular + envelope KEK); guarda de PII antes de persistir (M4 e M3); trilha de auditoria append-only pela aplicação (o código só insere; não há update/delete — para proteção plena em produção: COS com Object Lock/WORM, hash chain entre eventos e credencial exclusiva de escrita separada da CLOUDANT_APIKEY); acesso ao CPF somente via `vault.reveal()` auditado, com finalidade amarrada ao serviço chamador e consentimento ativo exigido para a consulta ao Serasa; endpoints internos protegidos por token secreto compartilhado (`X-Internal-Token`, comparado em tempo constante); hashes adicionais `prompt_sha256`/`output_sha256` nos traces para integridade (texto completo mantido para auditoria; evento de auditoria carrega apenas os hashes) |

---

### 2.2 Simulações e recomendações — Agente 2 (Parte B)

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Geração de cenários de quitação e recomendação personalizada via LLM |
| **Finalidade** | Oferecer ao titular cenários financeiros simulados e uma recomendação fundamentada para priorização de dívidas |
| **Base legal (LGPD)** | Art. 7º, I — consentimento do titular |
| **Categorias de titulares** | Pessoas físicas com consentimento ativo e snapshot financeiro gerado pelo Agente 1 |
| **Categorias de dados pessoais** | `pseudonym_id`; dados financeiros agregados do snapshot (saldos, parcelas, CET) — **nunca CPF, nome ou número de contrato** |
| **Dados sensíveis** | Não há categorias especiais no tratamento atual |
| **Destinatários** | IBM Cloud (Cloudant `decision_traces`, watsonx.ai para inferência) |
| **Retenção** | `expires_at` do documento (herdado do snapshot; ou `now + DECISION_RETENTION_DAYS` para snapshots legados sem `expires_at`); antecipado para `revoked_at + 90 dias` se o consentimento for revogado — regra a2 (ver [`docs/m4-retencao-e-delecao.md`](m4-retencao-e-delecao.md)) |
| **Medidas de segurança** | Guarda de PII (`assert_no_pii`) antes de persistir simulação e trace; prompt e output do LLM **mantidos** no trace (auditoria/explicabilidade); hashes adicionais `prompt_sha256`/`output_sha256` para integridade; eventos de auditoria `agent2.simulation_created` e `agent2.recommendation_generated` carregam apenas os hashes, nunca o texto; auditoria via `audit_bridge.py` (async→sync, tolerante a falha) |

---

### 2.3 Apagamento de dados (LGPD Art. 18)

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Atendimento a pedido de apagamento do titular |
| **Finalidade** | Cumprir o direito de apagamento; remover dados do titular de todos os bancos e tornar o CPF irrecuperável pela aplicação (o documento corrente não tem mais o material; cópias anteriores permanecem decifráveis enquanto a KEK existir — ver nota em `identity_vault.py`) |
| **Base legal** | LGPD Art. 18, VI — direito à eliminação dos dados pessoais tratados com o consentimento |
| **Titulares** | Pessoas físicas que solicitaram o apagamento |
| **Dados envolvidos** | Consentimentos, snapshots financeiros, decisões em `decision_traces` e cofre de identidade do titular |
| **Entrada do pedido** | CPF do titular, enviado só no corpo de `POST /internal/erase`. O sistema calcula o pseudônimo em **todas** as versões da chave de pseudonimização, para não deixar dados de versões anteriores à última rotação |
| **Prazo de execução** | Referência interna de 15 dias corridos. A LGPD fixa 15 dias para a declaração completa de acesso (Art. 19, II); para a eliminação o prazo é **[a definir pelo controlador]**, seguindo a regulamentação da ANPD |
| **Medidas** | Revogação de consentimentos (`LGPD_ERASURE`), deleção de snapshots, apagamento de decisões (`decisions_deleted`), crypto-shredding do cofre; auditoria em `consent.erasure_requested` e `erasure.completed` |

---

### 2.3.1 Acesso e portabilidade (LGPD Art. 18, II, V e VII)

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Atendimento a pedido de acesso, portabilidade e informação sobre compartilhamento |
| **Finalidade** | Entregar ao titular uma cópia completa dos dados que o sistema guarda dele, em formato legível por máquina |
| **Base legal** | LGPD Art. 18, II (acesso), V (portabilidade) e VII (informação sobre compartilhamento); Art. 19 (forma e prazo) |
| **Entrada do pedido** | CPF (todas as versões da chave) ou `pseudonym_id`, em `POST /internal/subject/export` |
| **Dados entregues** | Consentimentos, snapshots, decisões (inclusive o texto da recomendação gerado pelo modelo), metadados do cofre (nunca o CPF cifrado nem a chave), histórico de auditoria e resumo de compartilhamentos (instituições Open Finance, consultas ao Serasa, operadores) |
| **Prazo de execução** | 15 dias para a declaração completa (Art. 19, II) |
| **Medidas** | Evento `subject.access_exported` gravado antes da entrega (sem trilha, nada é entregue); token interno; CPF nunca devolvido nem logado |

---

### 2.4 Auditoria e governança

| Atributo | Detalhamento |
|---|---|
| **Nome da atividade** | Registro de trilha de auditoria |
| **Finalidade** | Conformidade regulatória; rastreabilidade de acesso e operações sobre dados do titular; suporte a investigações |
| **Base legal** | Art. 7º, II LGPD (cumprimento de obrigação legal); Art. 37 LGPD (registro de operações) |
| **Categorias de dados** | Apenas pseudônimos (`pseudonym_id`), identificadores de sessão (`trace_id`, `consent_id`, `snapshot_id`), tipo de evento e metadados de operação — **nunca CPF nem dados financeiros em claro** |
| **Retenção** | **[a definir pelo controlador]**. Hoje a trilha não é expurgada (padrão). O código já aplica qualquer prazo configurado em `AUDIT_RETENTION_DAYS` (regra f do job de retenção); as cópias no COS devem expirar pela regra de ciclo de vida do bucket com o mesmo prazo. Sugestão a validar com o jurídico: o maior prazo de guarda regulatória aplicável ao controlador, e não "para sempre" |
| **Natureza dos dados após o apagamento do titular** | Continuam sendo dado pessoal **pseudonimizado**: o `pseudonym_id` é recalculável a partir do CPF enquanto a chave HMAC existir. Por isso a trilha precisa de prazo definido e não pode ser tratada como dado anonimizado |
| **Destinatários** | Cloudant (banco `audit_trail`) + COS (bucket `desendividai-artifacts`, prefixo `audit/`) |

---

## 3. Compartilhamentos e operadores

| Terceiro | Papel | Dados compartilhados | Instrumento |
|---|---|---|---|
| IBM Cloud (Cloudant) | Operador de armazenamento | Consentimentos (pseudonimizados), snapshots, cofre (cifrado), trilha de auditoria | DPA com IBM |
| IBM Cloud Object Storage | Operador de armazenamento | Cópias JSON da trilha de auditoria (apenas pseudônimos) | DPA com IBM |
| IBM watsonx.ai | Operador de processamento | Contexto da sessão sem PII (pseudonymized IDs, dados financeiros agregados) | DPA com IBM |
| Open Finance Brasil (instituições) | Fonte de dados / parceiro | Consentimento OAuth + dados financeiros enviados pelas instituições | Termos BCB; Resolução Conjunta 3/2020 |
| Serasa Experian | Parceiro de dados | CPF via `vault.reveal()` — **Implementado (Parte B):** `HttpSerasaClient` real envia CPF no corpo do POST; sandbox envia apenas `pseudonym_id` | Contrato de fornecimento de dados [a definir pelo controlador] |

---

## 4. Direitos dos titulares (LGPD Art. 18 e Art. 20)

O canal de atendimento ao titular é do controlador; os endpoints abaixo são internos (protegidos por `X-Internal-Token`) e usados pela equipe de privacidade para cumprir o pedido.

| Direito | Base | Implementação | Estado |
|---|---|---|---|
| Confirmação da existência de tratamento | Art. 18, I | `POST /internal/subject/export` (lista vazia = nada guardado) | Implementado |
| Acesso aos dados | Art. 18, II | `POST /internal/subject/export` | Implementado |
| Correção de dados incompletos, inexatos ou desatualizados | Art. 18, III | Os dados vêm das fontes (Open Finance, Serasa): a correção é feita na origem e entra com um novo consentimento e um novo snapshot | Processo; sem endpoint |
| Anonimização, bloqueio ou eliminação de dados desnecessários ou excessivos | Art. 18, IV | Job de retenção (expurgo por prazo) e guarda de PII (bloqueia dados excessivos antes de persistir) | Implementado |
| Portabilidade | Art. 18, V | `POST /internal/subject/export` (JSON legível por máquina) | Implementado |
| Eliminação dos dados tratados com consentimento | Art. 18, VI | `POST /internal/erase` (por CPF, todas as versões da chave) e `POST /internal/erase/{pseudonym_id}` | Implementado |
| Informação sobre compartilhamento | Art. 18, VII | Bloco `sharing` da exportação + seção 3 deste ROPA | Implementado |
| Informação sobre a possibilidade de não consentir | Art. 18, VIII | Tela de consentimento do canal | Fora do escopo (canal) |
| Revogação do consentimento | Art. 18, IX | `DELETE /consent/{id}` (M1); a retenção antecipa o expurgo para `revoked_at + 90 dias` | Implementado (M1 + M4) |
| Revisão de decisões automatizadas | **Art. 20** | A recomendação é escolhida por regra determinística ([ADR 0001](adr/0001-recomendacao-por-regra-deterministica.md)) e o texto do modelo só a explica. A exportação entrega a decisão e a explicação; o pedido de revisão por pessoa é **[a definir pelo controlador]** | Parcial |

---

## 5. Medidas de segurança (LGPD Art. 46)

| Medida | Estado |
|---|---|
| Pseudonimização HMAC-SHA256 (chave mestra segregada) | Implementado (Parte A) |
| Envelope encryption AES-256-GCM (DEK por titular + KEK) | Implementado (Parte A) |
| Crypto-shredding (apagamento por destruição da DEK) | Implementado (Parte A) |
| Guarda de PII em todas as camadas de persistência e auditoria (M4 + M3) | Implementado (Parte A e B) |
| Controle de acesso ao CPF por `purpose` auditado (`SERASA_QUERY`, `LEGAL_REQUEST`) | Implementado (Parte A) |
| `purpose` amarrado ao serviço chamador e consentimento ativo exigido para `SERASA_QUERY`, com falha fechada | Implementado (revisão M4) |
| Re-identificação para Serasa via cofre; CPF apenas no corpo do POST | Implementado (Parte B) |
| Hashes adicionais `prompt_sha256`/`output_sha256` nos traces para integridade e correlação com auditoria; prompt e output mantidos no trace | Implementado (Parte B) |
| Retenção e apagamento cobrem `decision_traces` (regra a2, campo `decisions_deleted`) | Implementado (Parte B) |
| Autenticação IAM em Cloudant e COS | Implementado (Parte A) |
| Endpoints internos protegidos por `X-Internal-Token` (segredo compartilhado comparado em tempo constante; não é HMAC) | Implementado (Parte A) |
| Falhas de autenticação no `/internal/*` registradas como log estruturado (`internal.auth_failed`) | Implementado (revisão M4) |
| Job de retenção agendado na própria API (`RETENTION_SCHEDULE_ENABLED`) | Implementado (revisão M4); em produção, preferir job agendado único |
| Apagamento e exportação por CPF cobrindo todas as versões da chave de pseudonimização | Implementado (revisão M4) |
| Prazo de guarda da trilha de auditoria (`AUDIT_RETENTION_DAYS`) | Mecanismo implementado; **prazo a definir pelo controlador** |
| Relatório de Impacto à Proteção de Dados (RIPD, Art. 38) | **Rascunho** em [`m4-ripd.md`](m4-ripd.md) |
| Retry com backoff exponencial em operações Cloudant (429 too_many_requests) | Implementado (Parte B) |
| Gestão de chaves via Secrets Manager + HSM (Key Protect) | **Plano / produção** — implementar um KeyProvider sobre o Secrets Manager |
| Trusted Profile IAM (sem API key em texto plano) | **Plano / produção** |
| Eventos de auditoria como log JSON no stdout (`desendividai.audit`) | Implementado |
| Alertas de acesso anômalo sobre `event_type` (IBM Cloud Logs) | **Especificado, não ativo** — Cloud Logs e Activity Tracker não provisionados nesta conta |
| Envio do trace ao watsonx.governance | **Pendente** — `NoopGovernanceSink`; instância provisionada, falta implementar |

---

## 6. Histórico de revisões deste ROPA

| Versão | Data | Autor | Descrição |
|---|---|---|---|
| 1.0 | 2026-11-05 | Equipe M4 | Criação inicial — reflete Parte A do M4 |
| 2.0 | 2026-11-06 | Equipe M4 | Parte B: tratamento pelo Agente 2; re-identificação Serasa implementada; regra a2; erase cobre `decision_traces`; hashes de prompt/output |
| 3.0 | 2026-11-06 | Equipe M4 | Revisão: tabela de direitos corrigida (incisos do Art. 18 e Art. 20); acesso/portabilidade (2.3.1); apagamento por CPF em todas as versões da chave; prazo e natureza da trilha de auditoria; controles de acesso do cofre; token interno descrito corretamente; RIPD em rascunho |

---

## Referências no código

| Módulo | Caminho |
|---|---|
| Apagamento (Art. 18) | [`src/privacy/erasure.py`](../src/privacy/erasure.py) |
| Acesso e portabilidade (Art. 18) | [`src/privacy/subject_access.py`](../src/privacy/subject_access.py) |
| Agendador da retenção | [`src/privacy/retention_scheduler.py`](../src/privacy/retention_scheduler.py) |
| Endpoint interno | [`src/api/routers/internal.py`](../src/api/routers/internal.py) |
| Cofre de identidade | [`src/privacy/identity_vault.py`](../src/privacy/identity_vault.py) |
| Retenção | [`src/privacy/retention.py`](../src/privacy/retention.py) |
| Mapeamento de dados | [`docs/m4-mapeamento-dados.md`](m4-mapeamento-dados.md) |
| Ciclo de vida do consentimento | [`docs/m1-consent-lifecycle.md`](m1-consent-lifecycle.md) |
