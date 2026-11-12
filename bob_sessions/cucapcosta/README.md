# Sessões do IBM Bob — cucapcosta

Prints do resumo de consumo de cada tarefa do IBM Bob IDE usada neste projeto (entregável do guia *2H26 GuideBob_IBM SkillsBuild_AI Experiential Learning Lab BR*, p. 30–31).

**Como foram capturados:** no Bob IDE (v2.3.0), painel *Tasks* → abrir a tarefa → clicar no título para expandir o cabeçalho, que mostra *Context Length*, *Task Id*, *Workspace* e o custo em **Bobcoins**. Os prints 04 a 10 foram capturados em 12/11/2026.

- **Tarefas:** 10, em ordem cronológica (mais antiga primeiro), de 26 de out. a 11 de nov. de 2026.
- **Custo total:** 97.08 Bobcoins (soma dos valores abaixo)
- **Tokens** = tokens de contexto usados / janela de contexto do modelo (270.0k).

| Nº | Arquivo | Tarefa | Data | Custo (Bobcoins) | Tokens de contexto |
|---|---|---|---|---|---|
| 01 | [`01_feedback-arquitetura-ibm.png`](01_feedback-arquitetura-ibm.png) | Feedback da documentação e da arquitetura sobre o ecossistema IBM | 26 out | 0.262 | 36.9k / 270.0k (14%) |
| 02 | [`02_m1-setup-e-implementacao.png`](02_m1-setup-e-implementacao.png) | Implementação das tarefas do M1 a partir do `PLANO.md` | 27 out (retomada até 5 nov) | 22.01 | 115.4k / 270.0k (43%) |
| 03 | [`03_m1-branch-correcoes-e-pr.png`](03_m1-branch-correcoes-e-pr.png) | Branch do PLANO-M1, correções e Pull Request | 6 nov | 46.52 | 93.7k / 270.0k (35%) |
| 04 | [`04_cobertura-testes-store.png`](04_cobertura-testes-store.png) | Testes de `src/storage/store.py` (cobertura de 54% para 74%) | 11 nov | 1.06 | 46.9k / 270.0k (17%) |
| 05 | [`05_testes-simulation-client-repository-iam.png`](05_testes-simulation-client-repository-iam.png) | Testes de `simulation/client.py`, `agent1/repository.py` e `core/ibm_auth.py` | 11 nov | 0.837 | 32.0k / 270.0k (12%) |
| 06 | [`06_deprecationwarning-apiexception.png`](06_deprecationwarning-apiexception.png) | Troca de `ApiException.code` por `status_code` (DeprecationWarning) | 11 nov | 0.474 | 26.9k / 270.0k (10%) |
| 07 | [`07_revisao-lgpd-privacy.png`](07_revisao-lgpd-privacy.png) | Revisão de código LGPD de `src/privacy/` → [`docs/revisao-bob-privacy.md`](../../docs/revisao-bob-privacy.md) | 11 nov | 2.49 | 96.2k / 270.0k (36%) |
| 08 | [`08_fortalecer-testes-requisicoes.png`](08_fortalecer-testes-requisicoes.png) | Testes passam a verificar método, URL, headers e corpo das requisições | 11 nov | 1.84 | 66.4k / 270.0k (25%) |
| 09 | [`09_testes-ai-service-tool-agente2.png`](09_testes-ai-service-tool-agente2.png) | Testes de `simulation/ai_service.py` e da tool `run_simulation` do Agente 2 | 11 nov | 1.17 | 40.1k / 270.0k (15%) |
| 10 | [`10_correcoes-lgpd-e-feature-what-if.png`](10_correcoes-lgpd-e-feature-what-if.png) | Correção dos achados 1, 5, 6 e 7 da revisão LGPD, ajustes do code review e protótipo da simulação "e se" (`POST /simulation/what-if`, não incorporado) | 11 nov | 20.42 | 191.6k / 270.0k (71%) |

Todo código gerado nas tarefas 04 a 10 passou por revisão antes do commit: suíte completa, testes de mutação nos testes novos e code review independente. As correções feitas na revisão estão nas mensagens de commit do PR.
