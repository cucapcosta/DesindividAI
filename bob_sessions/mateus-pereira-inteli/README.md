# Sessões do IBM Bob — DesindividAI

Prints do resumo de consumo de cada tarefa do IBM Bob IDE usada neste projeto (entregável do guia *2H26 GuideBob_IBM SkillsBuild_AI Experiential Learning Lab BR*, p. 30–31).

**Como foram capturados:** no Bob IDE (v2.3.0), painel *Tasks* → abrir a tarefa → passar o mouse no indicador circular de contexto, ao lado do custo, no rodapé do chat. O popover *Context* mostra os tokens usados; o número ao lado do ícone de moeda é o custo da tarefa em **Bobcoins**. Capturado em 11/11/2026.

- **Tarefas:** 49, em ordem cronológica (mais antiga primeiro), de 5 e 6 de nov. de 2026. A tarefa de teste de conexão "Responda apenas: OK" ficou de fora.
- **Custo total:** 132.44 Bobcoins (soma dos valores abaixo)
- **⊗** = a tarefa parou porque atingiu o limite de custo por tarefa (*Cost limit reached*). Isso aconteceu em 16 das 49 tarefas.
- **Tokens** = tokens de contexto usados / janela de contexto do modelo (270.0k).

| Nº | Arquivo | Tarefa | Data | Custo (Bobcoins) | Tokens de contexto |
|---|---|---|---|---|---|
| 01 | [`01_parte-a-subtarefa-4-m4.png`](01_parte-a-subtarefa-4-m4.png) | Você vai executar a Parte A da Sub-Tarefa 4 (M4) do projeto DesindividAÍ | 5 nov | 4.15 ⊗ limite de custo (4.00) atingido | 88.8k / 270.0k (33%) |
| 02 | [`02_tarefa-pequena-pseudonymization.png`](02_tarefa-pequena-pseudonymization.png) | Tarefa pequena e isolada. Leia APENAS src/privacy/pseudonymization.py e tests/… | 5 nov | 0.176 | 16.4k / 270.0k (6%) |
| 03 | [`03_fase-3-identity-vault.png`](03_fase-3-identity-vault.png) | Execute a "Fase 3 — src/privacy/identity_vault.py" de docs/m4-plano-de-acao.md | 5 nov | 2.02 | 48.0k / 270.0k (18%) |
| 04 | [`04_fase-4-guarda-de-pii.png`](04_fase-4-guarda-de-pii.png) | Execute a "Fase 4 — Guarda de PII" de docs/m4-plano-de-acao.md + 3 ajustes | 5 nov | 3.05 | 80.4k / 270.0k (30%) |
| 05 | [`05_correcao-de-bug-pii-guard.png`](05_correcao-de-bug-pii-guard.png) | Correção de bug. Leia APENAS src/privacy/pii_guard.py, tests/m4/conftest.py e t… | 5 nov | 0.377 | 24.7k / 270.0k (9%) |
| 06 | [`06_fase-5-auditoria.png`](06_fase-5-auditoria.png) | Execute a "Fase 5 — Auditoria: src/privacy/audit.py" de docs/m4-plano-de-acao.md | 5 nov | 1.01 | 46.3k / 270.0k (17%) |
| 07 | [`07_correcoes-revisao-audit.png`](07_correcoes-revisao-audit.png) | Correções de revisão. Leia APENAS src/privacy/audit.py e tests/m4/test_audit.py | 5 nov | 1.02 ⊗ limite de custo (1.00) atingido | 38.4k / 270.0k (14%) |
| 08 | [`08_fase-6-retention.png`](08_fase-6-retention.png) | Execute a "Fase 6 — src/privacy/retention.py" de docs/m4-plano-de-acao.md | 5 nov | 1.79 | 66.1k / 270.0k (24%) |
| 09 | [`09_correcoes-revisao-retention.png`](09_correcoes-revisao-retention.png) | Correções de revisão em src/privacy/retention.py | 5 nov | 0.962 | 46.4k / 270.0k (17%) |
| 10 | [`10_fase-7-pontos-de-integracao.png`](10_fase-7-pontos-de-integracao.png) | Execute a "Fase 7 — Pontos de integração com M1 e M2" de docs/m4-plano-de-acao.md | 5 nov | 4.19 ⊗ limite de custo (4.00) atingido | 98.8k / 270.0k (37%) |
| 11 | [`11_continuacao-fase-7.png`](11_continuacao-fase-7.png) | Continuação da Fase 7 (docs/m4-plano-de-acao.md) | 5 nov | 2.21 | 70.2k / 270.0k (26%) |
| 12 | [`12_fase-8-testes-ponta-a-ponta.png`](12_fase-8-testes-ponta-a-ponta.png) | Execute a "Fase 8 — Testes ponta a ponta" de docs/m4-plano-de-acao.md | 5 nov | 1.85 | 73.2k / 270.0k (27%) |
| 13 | [`13_ajustes-test-integration-fase-8.png`](13_ajustes-test-integration-fase-8.png) | Ajustes em tests/m4/test_integration.py (Fase 8) | 5 nov | 0.371 | 37.8k / 270.0k (14%) |
| 14 | [`14_ajuste-pontual-test-integration-cleanup.png`](14_ajuste-pontual-test-integration-cleanup.png) | Ajuste pontual em tests/m4/test_integration.py, fixture cleanup (teardown) | 5 nov | 0.170 | 25.4k / 270.0k (9%) |
| 15 | [`15_fase-9-documentacao-primeira-metade.png`](15_fase-9-documentacao-primeira-metade.png) | Execute a PRIMEIRA metade da "Fase 9 — Documentação" de docs/m4-plano-de-acao.md | 5 nov | 2.39 | 77.0k / 270.0k (29%) |
| 16 | [`16_fase-9-correcoes-docs-m4.png`](16_fase-9-correcoes-docs-m4.png) | Fase 9 (docs/m4-plano-de-acao.md) — correções nos docs m4-* já criados | 5 nov | 2.74 | 83.0k / 270.0k (31%) |
| 17 | [`17_revisao-pr-m3.png`](17_revisao-pr-m3.png) | Revisão do PR do M3 (branch feat/m3-simulacao-recomendacao) | 6 nov | 3.38 | 67.9k / 270.0k (25%) |
| 18 | [`18_ajuste-complementar-pr-m3.png`](18_ajuste-complementar-pr-m3.png) | Ajuste complementar no PR do M3 (branch atual) | 6 nov | 1.02 | 34.4k / 270.0k (13%) |
| 19 | [`19_rebase-conflito-conftest.png`](19_rebase-conflito-conftest.png) | Há um rebase em andamento (NÃO rode nenhum comando git). Resolva SÓ o conflito… (1ª) | 6 nov | 0.191 | 18.3k / 270.0k (7%) |
| 20 | [`20_rebase-conflito-seed-test-consent.png`](20_rebase-conflito-seed-test-consent.png) | Há um rebase em andamento (NÃO rode nenhum comando git). Resolva SÓ o conflito… (2ª) | 6 nov | 0.195 | 15.6k / 270.0k (6%) |
| 21 | [`21_reescrever-secao-parte-b.png`](21_reescrever-secao-parte-b.png) | Tarefa: reescrever a seção "## Parte B (NÃO executar agora ...)" de docs/m4-plano-de-acao.md | 6 nov | 0.202 | 31.6k / 270.0k (12%) |
| 22 | [`22_fase-b1-retencao-e-apagamento.png`](22_fase-b1-retencao-e-apagamento.png) | Execute a "Fase B1 — Retenção e apagamento cobrem decision_traces" | 6 nov | 3.05 | 90.3k / 270.0k (33%) |
| 23 | [`23_ajuste-pontual-fase-b1.png`](23_ajuste-pontual-fase-b1.png) | Ajuste pontual da Fase B1. Leia APENAS src/privacy/retention_store.py e tests/m4/… | 6 nov | 0.551 | 41.2k / 270.0k (15%) |
| 24 | [`24_fase-b2-guarda-pii-agente-2.png`](24_fase-b2-guarda-pii-agente-2.png) | Execute a "Fase B2 — Guarda de PII e trilha de auditoria no Agente 2" | 6 nov | 2.40 | 68.8k / 270.0k (25%) |
| 25 | [`25_ajuste-pontual-fase-b2-pii-guard.png`](25_ajuste-pontual-fase-b2-pii-guard.png) | Ajuste pontual (Fase B2) na guarda de PII. Leia APENAS src/privacy/pii_guard.py … | 6 nov | 0.515 | 32.2k / 270.0k (12%) |
| 26 | [`26_fase-b3-reidentificacao-serasa.png`](26_fase-b3-reidentificacao-serasa.png) | Execute a "Fase B3 — Reidentificação para o Serasa real" | 6 nov | 1.15 | 52.3k / 270.0k (19%) |
| 27 | [`27_fase-b4-teste-ponta-a-ponta-cloud.png`](27_fase-b4-teste-ponta-a-ponta-cloud.png) | Execute a "Fase B4 — Teste ponta a ponta na cloud real" | 6 nov | 2.09 | 73.3k / 270.0k (27%) |
| 28 | [`28_ajuste-fase-b4-achado-execucao-real.png`](28_ajuste-fase-b4-achado-execucao-real.png) | Ajuste da Fase B4 (achado na execução real). Leia APENAS src/privacy/retention… | 6 nov | 1.30 ⊗ limite de custo (1.20) atingido | 54.1k / 270.0k (20%) |
| 29 | [`29_ajuste-minimo-test-retention.png`](29_ajuste-minimo-test-retention.png) | Ajuste mínimo. Leia APENAS tests/m4/test_retention.py | 6 nov | 0.455 ⊗ limite de custo (0.400) atingido | 40.1k / 270.0k (15%) |
| 30 | [`30_correcao-critica-teste-ponta-a-ponta.png`](30_correcao-critica-teste-ponta-a-ponta.png) | Correção crítica encontrada no teste ponta a ponta real | 6 nov | 0.627 | 41.6k / 270.0k (15%) |
| 31 | [`31_fase-b5-documentacao.png`](31_fase-b5-documentacao.png) | Execute a "Fase B5 — Documentação" de docs/m4-plano-de-acao.md | 6 nov | 3.15 ⊗ limite de custo (3.00) atingido | 90.8k / 270.0k (34%) |
| 32 | [`32_correcao-pontual-b4-recommendation-agent.png`](32_correcao-pontual-b4-recommendation-agent.png) | Correção pontual (B4) em src/agents/recommendation_agent.py | 6 nov | 1.15 | 50.0k / 270.0k (19%) |
| 33 | [`33_ajuste-cache-llm-event-loop.png`](33_ajuste-cache-llm-event-loop.png) | Ajuste complementar ao cache de LLM por event loop | 6 nov | 0.873 | 29.3k / 270.0k (11%) |
| 34 | [`34_duas-tarefas-pequenas.png`](34_duas-tarefas-pequenas.png) | Duas tarefas pequenas. PARE ao final. TAREFA 1 — testes (tests/test_recommen…) | 6 nov | 1.54 | 54.1k / 270.0k (20%) |
| 35 | [`35_correcao-b4-guarda-numeros-explicacao.png`](35_correcao-b4-guarda-numeros-explicacao.png) | Correção (B4) — guarda determinística de números na explicação do Agente 2 | 6 nov | 3.09 ⊗ limite de custo (3.00) atingido | 67.3k / 270.0k (25%) |
| 36 | [`36_correcao-pontual-explanation-guard.png`](36_correcao-pontual-explanation-guard.png) | Correção pontual em src/agents/explanation_guard.py | 6 nov | 0.446 | 27.2k / 270.0k (10%) |
| 37 | [`37_correcoes-review-pr-lote-1-a.png`](37_correcoes-review-pr-lote-1-a.png) | Correções de review do PR — LOTE 1. Leia em REVIEW_M4_TEMP.md (raiz)… (1ª) | 6 nov | 3.16 ⊗ limite de custo (3.00) atingido | 100.5k / 270.0k (37%) |
| 38 | [`38_correcoes-review-pr-lote-1-b.png`](38_correcoes-review-pr-lote-1-b.png) | Correções de review do PR — LOTE 1. Leia em REVIEW_M4_TEMP.md (raiz)… (2ª) | 6 nov | 3.63 ⊗ limite de custo (3.50) atingido | 104.5k / 270.0k (39%) |
| 39 | [`39_continuacao-lote-1.png`](39_continuacao-lote-1.png) | Continuação do LOTE 1 de REVIEW_M4_TEMP.md. R-01, R-05 e R-07 JÁ FORAM F… | 6 nov | 2.06 ⊗ limite de custo (2.00) atingido | 72.1k / 270.0k (27%) |
| 40 | [`40_ajuste-pontual-r-09-retention.png`](40_ajuste-pontual-r-09-retention.png) | Ajuste pontual no R-09 (src/privacy/retention.py) | 6 nov | 1.06 ⊗ limite de custo (1.00) atingido | 58.6k / 270.0k (22%) |
| 41 | [`41_correcoes-review-lote-2.png`](41_correcoes-review-lote-2.png) | Correções de review — LOTE 2 de REVIEW_M4_TEMP.md (itens R-03, R-06, R-10…) | 6 nov | 3.55 ⊗ limite de custo (3.50) atingido | 81.8k / 270.0k (30%) |
| 42 | [`42_correcoes-review-r-10-r-02-sobra-lote.png`](42_correcoes-review-r-10-r-02-sobra-lote.png) | Correções de review de REVIEW_M4_TEMP.md: itens R-10 e R-02 (sobra do Lote …) | 6 nov | 6.15 ⊗ limite de custo (6.00) atingido | 107.2k / 270.0k (40%) |
| 43 | [`43_correcoes-review-sobras-lote-3-lote-4.png`](43_correcoes-review-sobras-lote-3-lote-4.png) | Correções de review de REVIEW_M4_TEMP.md — sobras do Lote 3 + Lote 4 | 6 nov | 7.13 ⊗ limite de custo (7.00) atingido | 104.9k / 270.0k (39%) |
| 44 | [`44_correcoes-review-lote-4-r16-r17-r24.png`](44_correcoes-review-lote-4-r16-r17-r24.png) | Correções de review de REVIEW_M4_TEMP.md — Lote 4: R-16, R-17, R-24 | 6 nov | 9.23 ⊗ limite de custo (9.00) atingido | 138.0k / 270.0k (51%) |
| 45 | [`45_correcoes-review-lote-4-parte-c.png`](45_correcoes-review-lote-4-parte-c.png) | Correções de review de REVIEW_M4_TEMP.md — Lote 4 (parte C: R-20, R-21, R-…) | 6 nov | 12.22 ⊗ limite de custo (12.00) atingido | 161.8k / 270.0k (60%) |
| 46 | [`46_correcao-rodada-anterior-r20-r25.png`](46_correcao-rodada-anterior-r20-r25.png) | Correção da rodada anterior (R-20…R-25 de REVIEW_M4_TEMP.md) | 6 nov | 4.50 | 85.3k / 270.0k (32%) |
| 47 | [`47_correcoes-review-lote-6-limpeza.png`](47_correcoes-review-lote-6-limpeza.png) | Correções de review — LOTE 6 (limpeza) de REVIEW_M4_TEMP.md | 6 nov (retomada em 11 nov) | 15.10 | ~134.4k / 270.0k (50%) |
| 48 | [`48_continuacao-lote-6-limpeza.png`](48_continuacao-lote-6-limpeza.png) | Continuação do LOTE 6 (limpeza) de REVIEW_M4_TEMP.md | 6 nov | 8.60 | 105.1k / 270.0k (39%) |
| 49 | [`49_corrigir-achados-review-m4-lgpd.png`](49_corrigir-achados-review-m4-lgpd.png) | Você vai corrigir achados de uma review do M4 (privacidade/LGPD) neste repositório | 6 nov | 0.204 | 35.7k / 270.0k (13%) |

## Observações

- A tarefa 47 foi retomada em 11/11, por isso aparece no grupo *Today* do Bob e o custo dela inclui o uso desse dia.
- Os títulos aparecem truncados no Bob; o texto acima vem do cabeçalho de cada tarefa, que pode estar cortado ("…").
- **Conferência do total:** o popover *Budget* do Bob (passando o mouse sobre o custo) mostrava **132.45 / 150.00** Bobcoins usados no plano. A soma das 49 tarefas dá 132.44; a diferença de 0.01 provavelmente vem do arredondamento ou da tarefa de teste "Responda apenas: OK", que ficou de fora.
- Os números foram lidos dos prints. Não foi possível conferir com um export JSON (`bob-task-*.json`): o único export que existia na raiz do repo não estava mais na pasta na hora da conferência.
