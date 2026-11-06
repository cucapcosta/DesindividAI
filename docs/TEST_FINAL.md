# TEST_FINAL — Relatório de Testes Final do DesendividAÍ

> Validação de ponta a ponta do projeto (M1 stub → M2 Agente 1 → M3 Agente 2 → M4 Privacidade/LGPD), executada em **2026-11-06** na branch `feat/m4-parte-a` (rebaseada sobre `develop` com o merge do M3).
> Todos os resultados abaixo vêm de **execuções reais**: servidor FastAPI local, IBM Cloudant, IBM Cloud Object Storage e watsonx.ai (Granite) reais, configurados apenas pelo `.env`.
> Este documento não contém segredos, valores do `.env` nem CPFs.

---

## 1. Resumo executivo

| Camada | Comando | Resultado |
|---|---|---|
| Unitários + API (fakes/in-memory) | `.venv/bin/python -m pytest -q` | **408 passed, 1 skipped**, 7 deselected |
| Integração na cloud real (M4) | `.venv/bin/python -m pytest -q -m integration` | **7 passed** (~80 s) |
| Smoke na cloud real (M3) | `RUN_CLOUD_TESTS=1 .venv/bin/python -m pytest -q tests/test_cloud_integration.py` | **1 passed** |
| E2E manual no servidor real (roteiro do orquestrador) | 37 cenários HTTP + verificações diretas no Cloudant/COS | **37/37 OK** |
| Validação estatística da explicação (Granite real) | 16 + 10 recomendações reais, analisadas uma a uma | **0 respostas entregues com número inventado** |

A validação final encontrou e **corrigiu** 5 defeitos reais (seção 7):
1. 429 no Cloudant;
2. auditoria do cofre desligada em produção;
3. cache do LLM preso a um event loop;
4. dependência síncrona que anulava esse cache;
5. **o Granite inventava valores em R$** em cerca de 10–20% das respostas.

Depois de cada correção, todas as camadas foram executadas de novo e passaram por completo.

**Veredito:** o fluxo completo funciona de ponta a ponta. A camada de privacidade do M4 cobre o Agente 1 e o Agente 2 da seguinte forma:
- pseudonimização;
- cofre de identidade com crypto-shredding;
- guarda de PII;
- auditoria imutável (Cloudant + COS);
- retenção;
- direito ao apagamento.

Uma guarda determinística garante que toda explicação entregue ao cliente usa somente números calculados pelo motor.

---

## 2. Estratégia de testes

O projeto é testado em três camadas complementares.

1. **Unitários e de API (offline).** Rodam com fakes e repositórios in-memory (`tests/fakes.py`, `tests/m4/conftest.py`), sem rede. Cobrem regras de negócio (CET, consentimento, motor de simulação), os agentes com LLM falso, os routers via `TestClient` e todos os componentes do M4. Os testes que dependem de cloud são excluídos por padrão (`pytest.ini`: `addopts = -m "not integration"`).
2. **Integração (cloud real).** São os testes marcados com `@pytest.mark.integration`. Usam Cloudant, COS e watsonx reais, com CPF de teste aleatório e válido, e uma fixture de limpeza que remove consentimentos, snapshots, decisões, cofre e objetos COS criados. Eventos de auditoria permanecem, por desenho (seção 8).
3. **E2E no servidor real (este relatório).** O roteiro sobe `uvicorn`, faz chamadas HTTP como um cliente faria e depois inspeciona diretamente o Cloudant e o COS. O objetivo é provar as propriedades de privacidade: ausência de PII, hashes, auditoria, cofre cifrado e apagamento.

Princípios seguidos:
- Nenhum teste imprime CPF ou segredo.
- Os IDs são UUID ou pseudônimo (HMAC-SHA256 com 64 hex).
- Cada teste de cloud limpa o que cria.
- A validação final foi repetida depois de cada correção.

---

## 3. Pré-requisitos de ambiente

- Python 3.11+ com o virtualenv do projeto em `.venv` (`pip install -r requirements.txt`).
- Um `.env` preenchido a partir do `.env.example`. Nomes das variáveis (sem valores):

  | Grupo | Variáveis |
  |---|---|
  | watsonx.ai | `WATSONX_API_KEY`, `WATSONX_URL`, `WATSONX_PROJECT_ID`, `WATSONX_MODEL_ID` |
  | Cloudant | `CLOUDANT_URL`, `CLOUDANT_API_KEY`, `CLOUDANT_DB_*` (vazio = nome padrão) |
  | COS (auditoria) | `COS_*` (endpoint, bucket, credenciais) |
  | M4 — chaves | `PSEUDONYM_KEY` / chaves versionadas, `VAULT_KEK` (KEK do cofre) |
  | M4 — API interna | `INTERNAL_API_TOKEN` (sem ele, `/internal/*` responde 503) |
  | Fontes de dados | `OPEN_FINANCE_SANDBOX=true`, `SERASA_SANDBOX=true` |

- Os bancos e índices do Cloudant são criados de forma idempotente por `ensure_databases()` / `ensure_indexes()` (em `src/privacy/retention_store.py`), que agora fazem retry em 429.

---

## 4. Como executar cada camada

```bash
# 1) Unitários + API (offline)
.venv/bin/python -m pytest -q

# 2) Integração na cloud real (usa o .env)
.venv/bin/python -m pytest -q -m integration

# 2b) Smoke do M3 na cloud real
RUN_CLOUD_TESTS=1 .venv/bin/python -m pytest -q tests/test_cloud_integration.py

# 3) E2E manual
.venv/bin/uvicorn src.main:app --port 8765                 # terminal 1
.venv/bin/python scripts/seed_test_consent.py create       # devolve um consent_id AUTHORISED
curl "localhost:8765/debts?consent_id=<consent_id>"        # Agente 1 → snapshot_id
curl -X POST localhost:8765/simulation \
     -H 'Content-Type: application/json' \
     -d '{"snapshot_id":"<snapshot_id>","monthly_budget":6000}'
curl -X POST localhost:8765/recommendation \
     -H 'Content-Type: application/json' \
     -d '{"simulation_id":"<simulation_id>","language_level":"simple"}'
curl -X POST localhost:8765/internal/erase/<pseudonym_id> -H "X-Internal-Token: $INTERNAL_API_TOKEN"
.venv/bin/python scripts/seed_test_consent.py delete <consent_id>

# Retenção (dry-run por padrão na API; CLI exige a flag)
curl -X POST "localhost:8765/internal/retention/run" -H "X-Internal-Token: $INTERNAL_API_TOKEN"
.venv/bin/python -m src.privacy.retention --dry-run
```

> Só rode `dry_run=false` / sem `--dry-run` quando quiser apagar de fato os dados vencidos.

---

## 5. Casos de teste E2E (servidor real) — 37/37 OK

Execução: 2026-11-06, servidor local em `:8765`, Granite real, Cloudant/COS reais, consentimento de teste criado por `seed_test_consent.py` (CPF do sandbox; nenhum CPF aparece nas respostas nem nos logs).

### 5.1 Plataforma

| ID | Cenário | Esperado | Obtido | Status |
|---|---|---|---|---|
| P1 | `GET /health` | 200 `{"status":"ok"}` | 200 | ✅ |
| P2 | Swagger `/docs` | 200 | 200 | ✅ |
| P3 | OpenAPI expõe todos os endpoints | `/consent`, `/debts`, `/simulation`, `/recommendation`, `/internal/*` | `/consent`, `/consent/{consent_id}`, `/debts`, `/internal/erase/{pseudonym_id}`, `/internal/retention/run`, `/recommendation`, `/simulation` | ✅ |
| P4 | `POST /consent` (M1) | 501 (stub do M1) | 501 | ✅ |
| C1 | Seed de consentimento AUTHORISED via cofre | `consent_id` UUID; CPF guardado só no cofre | UUID gerado; pseudônimo vem de `IdentityVault.store()` | ✅ |

### 5.2 Agente 1 (M2) — coleta e análise de dívidas

| ID | Cenário | Esperado | Obtido | Status |
|---|---|---|---|---|
| A1 | `GET /debts` com Granite real | 200, dívidas consolidadas | 200, **6 dívidas**, total **R$ 63.914,50**, ~11 s | ✅ |
| A2 | Ordenação por CET (`priority_rank`) | Maior CET primeiro | Maior CET: cartão de crédito Nubank, 369,07% a.a. | ✅ |
| A3 | Consentimento inexistente | 403/404 | 404 `CONSENT_NOT_FOUND` | ✅ |
| A4 | `consent_id` malformado | 422 | 422 | ✅ |

### 5.3 Agente 2 (M3) — simulação e recomendação

| ID | Cenário | Esperado | Obtido | Status |
|---|---|---|---|---|
| S1 | `POST /simulation` com orçamento de R$ 6.000 | 3 cenários na ordem aggressive / consolidation / minimum_viable | aggressive: **13 meses**, juros R$ 9.255,86 · consolidation: **12 meses**, juros R$ 7.494,75 · minimum_viable: **53 meses**, juros R$ 27.789,22 | ✅ |
| S2 | Orçamento abaixo da soma dos mínimos (R$ 1.500) | Aviso explícito nos cenários | Aviso "menor que a soma dos pagamentos mínimos" nos 3 | ✅ |
| S3 | Orçamento 0 | 422 | 422 | ✅ |
| S4 | Snapshot inexistente | 404 | 404 `SNAPSHOT_NOT_FOUND` | ✅ |
| R1 | `POST /recommendation` (`simple`) | 200, explicação em linguagem simples, `trace_id` | Recomenda **consolidation**; texto em PT-BR acessível; `trace-…` | ✅ |
| R2 | `POST /recommendation` (`technical`) | 200, explicação técnica | Recomenda **consolidation**; cita a portabilidade de 4 dívidas com CET > 30% a.a. | ✅ |
| R3 | Recomendação com orçamento abaixo dos mínimos (**5 execuções**, porque o LLM não é determinístico) | Nenhuma afirmação positiva de economia ("economiza R$ X"); negativas como "não economiza" são aceitas | 0 afirmações de economia em 5 respostas | ✅ |
| R4 | Simulação inexistente | 404 | 404 `SIMULATION_NOT_FOUND` | ✅ |

### 5.4 M4 — propriedades de privacidade verificadas direto no Cloudant/COS

| ID | Cenário | Esperado | Obtido | Status |
|---|---|---|---|---|
| M1 | Snapshot persistido | Sem PII; `key_version`; `expires_at` | `key_version=v1`; `expires_at` em +180 dias; `assert_no_pii` passa | ✅ |
| M2 | Decisões em `decision_traces` | 2 simulações + 7 traces (R1, R2 e 5× R3) com retenção herdada | 9 docs; `expires_at` e `consent_id` iguais aos do snapshot | ✅ |
| M3 | Integridade dos traces | `prompt_sha256` / `output_sha256` conferem com o `prompt`/`output` gravados | Hashes recalculados batem com o texto gravado | ✅ |
| M4 | Trilha de auditoria do fluxo | 1× `agent1.snapshot_persisted`, 2× `agent2.simulation_created`, 7× `agent2.recommendation_generated`; sem texto | Exatamente 1 / 2 / 7; nenhum texto de prompt ou resposta nos eventos | ✅ |
| M5 | Cópia imutável no COS | 1 objeto por evento | 10/10 objetos encontrados | ✅ |
| M6 | Cofre de identidade | CPF só cifrado (envelope encryption) | Campos: `cpf_ciphertext`, `nonce`, `wrapped_dek`, `dek_nonce`, `kek_version`, `key_version`… — nenhum CPF em claro | ✅ |

### 5.5 M4 — API interna e retenção

| ID | Cenário | Esperado | Obtido | Status |
|---|---|---|---|---|
| I1 | `/internal/*` sem token | 401 | 401 | ✅ |
| I2 | Token errado | 401 (`hmac.compare_digest`) | 401 | ✅ |
| I3 | `/internal/erase/<CPF>` (CPF na URL) | 422 (só aceita pseudônimo hex64) | 422 | ✅ |
| I4 | Retenção via API sem parâmetro | `dry_run=true` por padrão; nada apagado | `dry_run: true`, 0 apagados, `skipped_invalid: 16` (legado do M3, ver seção 8) | ✅ |
| I5 | Retenção via CLI `--dry-run` | exit 0, relatório JSON | exit 0, mesmo relatório | ✅ |

### 5.6 M4 — direito ao apagamento (LGPD Art. 18)

| ID | Cenário | Esperado | Obtido | Status |
|---|---|---|---|---|
| E1 | `POST /internal/erase/{pseudonym_id}` | Revoga, apaga e destrói o cofre | `consents_revoked: 1`, `snapshots_deleted: 1`, `decisions_deleted: 9`, `vault_shredded: true` | ✅ |
| E2 | `GET /debts` depois do erase | Bloqueado | 403 `CONSENT_NOT_AUTHORISED` | ✅ |
| E3 | `POST /simulation` depois do erase | Snapshot sumiu | 404 `SNAPSHOT_NOT_FOUND` | ✅ |
| E4 | `POST /recommendation` depois do erase | Simulação sumiu | 404 `SIMULATION_NOT_FOUND` | ✅ |
| E5 | Cofre depois do erase | `shredded_at` preenchido; material de chave removido | `shredded_at` presente | ✅ |
| E6 | CPF recuperável? | Não (crypto-shredding) | `reveal` → `VaultError` | ✅ |
| E7 | Erase idempotente | 2ª chamada não faz nada | 0 / 0 / 0 / `false` | ✅ |
| E8 | Auditoria do apagamento | `consent.erasure_requested`, `erasure.completed`, `vault.reveal` | Presentes (2 erases → 2 + 2; 1 `vault.reveal`) | ✅ |
| E9 | Limpeza do seed | Consentimento de teste removido | Removido; o cofre já estava destruído (`false`, esperado) | ✅ |

### 5.7 Validação estatística da explicação do Agente 2 (Granite real)

Como o LLM não é determinístico, um único E2E não basta. Foram feitas mais duas baterias no servidor real. Em cada resposta, a explicação **entregue** foi analisada e o trace foi lido no Cloudant (`explanation_source`, `guard_issues`).

| Bateria | Respostas | Guarda disparou na 1ª tentativa | Origem do texto entregue | Problemas na resposta entregue |
|---|---|---|---|---|
| Orçamento R$ 6.000 (simple/technical) | 8 | 2× `UNGROUNDED_AMOUNT` | 6 `llm`, 1 `llm_retry`, 1 `fallback` | **0** |
| Orçamento R$ 1.500, abaixo dos mínimos | 8 | 1× `UNGROUNDED_AMOUNT` + `SAVINGS_CLAIM_BELOW_MINIMUMS` | 7 `llm`, 1 `fallback` | **0** |
| Diagnóstico, orçamento R$ 6.000 | 10 | 2× `UNGROUNDED_AMOUNT` | — | — |

O diagnóstico confirmou, contra o plano do mês 1 calculado pelo motor, que os disparos eram **alucinações reais**, e não falsos positivos:
- "parcelas de R$ 427,55 (financiamento de veículo)": o valor real é R$ 1.350,60.
- "destinará R$ 1.681,42 ao orçamento restante": esse valor não existe no plano.
- "A consolidação economiza R$ 49.335,04 … comparado a pagar só os mínimos": com o orçamento abaixo dos mínimos, o motor calcula economia **negativa** (−R$ 21.407,60), e a própria subtração feita pelo LLM estava errada (o correto seria R$ 49.336,69).

---

## 6. Testes automatizados

### 6.1 Unitários / API — 408 passed, 1 skipped

Testes coletados por área (inclui casos parametrizados):

| Área | Arquivos | Testes coletados |
|---|---|---|
| M2 — Agente 1 | `test_agent1.py`, `test_analysis.py`, `test_cet.py`, `test_consent_rules.py`, `test_debts_api.py`, `test_sources.py`, `test_pseudonymization.py` | 76 |
| M3 — Agente 2 | `test_engine.py`, `test_api.py`, `test_config_store.py`, `test_cloud_integration.py` (o skip) | 55 |
| Agente 2 — guarda da explicação e cache do LLM (B4) | `test_explanation_guard.py`, `test_recommendation_llm_cache.py` | 51 |
| M4 — Privacidade | `tests/m4/` (12 arquivos) | 227 |
| **Total** | | **409** (408 passed + 1 skipped) |

A guarda da explicação é coberta por:
- **`test_explanation_guard`**:
  - parser de valores em R$, com 12 formatos (incluindo os 5 que falharam numa versão intermediária);
  - o caso real do R$ 49.335,04;
  - a negativa "não economiza";
  - fallback para simple/technical, com e sem orçamento abaixo dos mínimos;
  - fluxo via API: `llm`, `llm_retry` e `fallback`.
- **`test_recommendation_llm_cache`**:
  - mesmo loop → mesmo cliente;
  - loops distintos → clientes distintos;
  - falha não fica em cache;
  - fora de loop → sem cache.

Cobertura do M4, por arquivo:
- **`test_pii_guard`**: CPF em 4 formatos, inclusive com espaços, e e-mail/telefone. A varredura em massa teve 0 falhas em 20.000 CPFs e 0 falsos positivos em 5.000 textos financeiros.
- **`test_identity_vault`**: envelope encryption, reveal com finalidade, shred e auditoria ligada em produção.
- **`test_key_provider`**: chaves versionadas.
- **`test_pseudonymization_regression`**: pseudônimo estável.
- **`test_audit`**: Cloudant + COS, tolerância a falha.
- **`test_retention`**: regras a, a2, b, c e d, `dry_run`, retry 429.
- **`test_erasure`**: ordem, idempotência, isolamento entre titulares.
- **`test_internal_api`**: 503/401/422, `dry_run` padrão.
- **`test_persist_snapshot_privacy`**: snapshot com PII nunca é salvo.
- **`test_agent2_privacy`**: guarda e hash no Agente 2.
- **`test_serasa_reidentification`**: CPF só no corpo do POST ao Serasa real.

O 1 skip é intencional: `tests/test_cloud_integration.py::test_cloud_smoke` (smoke do M3 contra a cloud real) só roda com `RUN_CLOUD_TESTS=1`. Ele foi executado à parte nesta validação (seção 6.3).

### 6.2 Integração na cloud real — 7 passed

| Teste | O que prova |
|---|---|
| `test_ensure_databases_and_indexes` | Bancos e índices criados de forma idempotente (com retry 429) |
| `test_vault_roundtrip_real` | store → reveal → shred no Cloudant real |
| `test_audit_cloudant_and_cos` | Evento gravado no Cloudant e no COS |
| `test_debts_end_to_end` | `/debts` real com snapshot sem PII + auditoria |
| `test_internal_erase_real` | Erase real com contagens e cofre destruído |
| `test_retention_dry_run_real` | Relatório de retenção real sem apagar nada |
| `test_full_flow_m2_m3_m4` | `/debts` → `/simulation` → `/recommendation` → `/internal/erase`, com auditoria `agent1.*`/`agent2.*`, hashes e apagamento das decisões |

Os 7 testes acima ficam em `tests/m4/test_integration.py` e passaram em várias execuções seguidas sem deixar resíduo novo.

### 6.3 Smoke do M3 na cloud real — 1 passed

`RUN_CLOUD_TESTS=1 pytest tests/test_cloud_integration.py` cobre Cloudant, Granite, `POST /simulation` e `POST /recommendation` (simple e technical) via `TestClient`, com limpeza dos documentos criados.

Na primeira execução da validação final, **falhou** (defeito 4 da seção 7). Depois da correção, passou.

---

## 7. Defeitos encontrados e corrigidos

| # | Defeito | Impacto | Correção | Verificação |
|---|---|---|---|---|
| 1 | O Cloudant Lite devolvia **429 `too_many_requests`** em `ensure_indexes`/`ensure_databases` e nas consultas do `CloudantRetentionStore` | Testes de integração e o job de retenção falhavam de forma intermitente | `_with_429_retry` com backoff exponencial (até 5 tentativas, teto de 8 s) em `src/privacy/retention_store.py` | Testes unitários de 429 com `sleep` falso; integração 7/7 repetida |
| 2 | **`get_identity_vault()` criava o cofre sem callback de auditoria** | Em produção, nenhum `vault.reveal`/`vault.shred` chegava ao `audit_trail`; só os testes, que injetam audit, viam os eventos | `_default_audit()` tolerante liga `get_audit_logger().record_event`; uma falha de auditoria nunca quebra o cofre | Novo teste unitário; E8 confirma `vault.reveal` no Cloudant real |
| 3 | Um teste de 429 dormia 3 s de verdade | Suíte lenta | `time.sleep` substituído por monkeypatch | Suíte unitária voltou ao tempo normal |
| 4 | **Regressão do cache do LLM:** o `lru_cache` em `build_llm` (introduzido na revisão do PR do M3) guardava um `ChatWatsonx` cujo cliente HTTP async ficava preso ao 1º event loop | Qualquer contexto com mais de um loop (o smoke do M3 com `TestClient`, scripts com `asyncio.run`) dava `RuntimeError: Event loop is closed`, convertido em **503** na 2ª recomendação. No uvicorn, que tem um loop só, não aparecia | Cache **por event loop** (`WeakKeyDictionary`); falha nunca fica em cache; `clear_llm_cache()` | Reprodução direta (2× `asyncio.run`) passou; smoke do M3 1 passed; 4 testes novos |
| 5 | A dependência `Depends(build_llm)` era síncrona, e o FastAPI a executa num threadpool sem loop | O cache do item 4 nunca seria usado no servidor (cliente recriado a cada requisição) | Dependência async `get_llm()` no router; testes passam a sobrescrever `get_llm` | Teste: `await get_llm()` 2× no mesmo loop → 1 construção |
| 6 | **O Granite inventava valores em R$** na explicação: parcelas erradas, valores inexistentes e, com orçamento abaixo dos mínimos, uma "economia" calculada por ele próprio, mesmo com o prompt proibindo | Cliente recebendo número financeiro falso (≈ 10–20% das respostas nas baterias da seção 5.7) | Guarda determinística `src/agents/explanation_guard.py`. Todo valor em R$ do texto precisa existir nos FATOS (±R$ 1); com orçamento abaixo dos mínimos, nenhuma afirmação positiva de economia. Se reprovar → **1 nova tentativa** → senão **fallback determinístico** montado só com os FATOS. O trace registra `explanation_source` (`llm`/`llm_retry`/`fallback`) e `guard_issues` (só códigos); `output`/`output_sha256` correspondem ao texto entregue | Seção 5.7: 0 respostas entregues com problema em 16; R3 (5×) OK; 51 testes |
| 7 | Bug na 1ª versão da guarda: o parser lia "R$ 1500,00" como 150 e "R$ 7494,75" como 749 (regex casava só um prefixo) | Falsos positivos mandariam respostas corretas para retry/fallback | Captura do token numérico inteiro + 12 casos parametrizados | Testes + baterias reais da seção 5.7 |

---

## 8. Resíduos conhecidos na cloud

| Onde | O quê | Origem | Ação |
|---|---|---|---|
| `financial_snapshots` | 13 documentos legados | Smoke/seed do M3 antes do M4 | Ignorados pela retenção (`skipped_invalid`). **A exclusão depende de decisão do responsável.** |
| `decision_traces` | 3 documentos com pseudônimo de exemplo e sem `expires_at` | M3 antes do M4 | Idem |
| COS + `audit_trail` | 5 objetos + 2 eventos das primeiras execuções da Fase 8 | Antes da correção da limpeza | Idem |
| `audit_trail` + COS | Eventos dos testes E2E/integração | Por desenho | **A trilha de auditoria é imutável e nunca é expurgada.** Os eventos não contêm CPF, só pseudônimo e IDs. |

Os 16 `skipped_invalid` do I4/I5 são exatamente esses documentos legados (13 + 3).

---

## 9. Definição de Pronto — projeto

- [x] Agente 1 coleta, analisa e persiste snapshot pseudonimizado com Granite real
- [x] Agente 2 simula 3 cenários e gera recomendação explicável (simples e técnica)
- [x] Nenhuma PII em snapshots, decisões, traces, eventos ou logs (guarda + verificação direta)
- [x] CPF apenas no cofre, com envelope encryption; reidentificação só com finalidade declarada
- [x] Auditoria imutável (Cloudant + COS) de todo o fluxo, incluindo o cofre em produção
- [x] Integridade dos traces do Agente 2 por hash SHA-256
- [x] Retenção automática (snapshots, decisões, consentimentos, cofre) com `dry_run` seguro por padrão
- [x] Direito ao apagamento (Art. 18) idempotente, cobrindo consentimentos, snapshots, decisões e cofre
- [x] API interna protegida por token e sem aceitar CPF na URL
- [x] Explicação do Agente 2 só com números do motor (guarda determinística + nova tentativa + fallback), validada com Granite real
- [x] Suíte unitária verde (408 passed, 1 skipped), integração real verde (7/7), smoke do M3 verde (1/1), E2E real 37/37
- [x] Documentação LGPD do M4 (mapeamento, ROPA, retenção, chaves/IAM, auditoria) atualizada
