# Relatório de uso do IBM Bob (CLI) – DesendividAÍ

Nov 11, 2026 · @Igor Passo

## Resumo executivo

O Bob foi usado pelo terminal (Bob Shell) em uma única janela de trabalho, em 05/11/2026 entre 10:11 e 11:49, e consumiu **5,777 créditos** em **19 tarefas** no projeto DesendividAÍ.

- **Escopo:** Sub-Tarefa 3 (M3) do projeto: motor de simulação e Agente 2 de recomendação (FastAPI, LangChain, Granite no watsonx.ai, Cloudant).
- **O que o Bob fez:** 3 revisões somente leitura (2 de segurança e 1 code review), 10 edições pontuais de código, contrato e teste, 2 documentos (ADR e diagramas), 1 rodada de commits e 3 testes de sanidade.
- **Atividade:** 147 respostas do modelo e 184 chamadas de ferramenta.
- **Onde o gasto se concentrou:** 42% nas revisões e 35% na criação dos commits.

## Como o Bob foi usado

O uso foi todo pelo CLI, não pela IDE. Por isso as tarefas não aparecem no painel Tasks do Bob IDE: ficam só no banco local do Bob Shell.

| Item | Detalhe |
| --- | --- |
| Interface | Bob Shell (CLI), workspace `desendividai` |
| Modelo | `premium-ide` (contexto de 270 mil tokens, saída máxima de 64 mil) |
| Modo `ask` (somente leitura) | 4 tarefas: 3 revisões e 1 teste de sanidade |
| Modo `agent` (edita arquivos e roda comandos) | 15 tarefas |
| Ferramentas | `read_file` (101), `execute_command` (32), `grep` (17), `list_files` (15), `apply_diff` (10), `search_and_replace` (2), `update_todo_list` (2), `glob` (2), `write_file` (2), `insert_content` (1) |

**Padrão de prompts.** Os pedidos de edição eram longos e prescritivos: diziam o arquivo, a função e a mudança exata e terminavam com "não altere mais nada". Cada pedido virou uma tarefa pequena e isolada, o que manteve o custo baixo (entre 0,06 e 0,18 por edição). Nas revisões, o prompt listava os arquivos, os critérios a verificar e o formato esperado de cada achado: severidade, `arquivo:linha`, exemplo e correção. Pedia também que o Bob não inventasse problemas e dissesse quando algo estava correto.

**Controles nos prompts.** O pedido de commits proibia `git add -A`, `git add .` e `git commit -a`, e exigia adicionar por caminho explícito.

## O que foi desenvolvido

O trabalho seguiu o ciclo revisar, corrigir, documentar e commitar.

### 1. Revisões somente leitura (modo `ask`)

- **Revisão de segurança, duas rodadas (10:11 e 10:22).** Cobriu LGPD, injeção de prompt, vazamento de PII, segredos e erros, controle de acesso e IDOR, injeção em consultas Mango do Cloudant, SSRF e DoS. A segunda rodada ampliou os arquivos e usou 14 buscas com `grep`.
- **Code review (11:02).** Cobriu lógica financeira do motor (juros, ordem de pagamento, consolidação com PMT, baseline, rateio), concorrência e async, divergências com o contrato OpenAPI, códigos HTTP, lacunas de teste e código morto.

### 2. Correções vindas das revisões (modo `agent`)

- `src/api/models.py`: `monthly_budget` passou a aceitar `gt=0` e `le=1_000_000`.
- `src/simulation/client.py`: a simulação local passou a rodar em `asyncio.to_thread`, para não bloquear o event loop.
- `src/simulation/engine.py`: o horizonte e as parcelas ganharam limite (`MAX_MONTHS`).
- `src/services/simulation_service.py` e `.env.example`: o banco de decisões ficou separado dos snapshots (`decision_traces`).
- `src/storage/store.py`: as URLs do `CloudantStore` passaram a ser codificadas e o erro HTTP passou a ser tratado por um helper `_check`.
- `src/services/recommendation_service.py`: o helper `_safe_text` passou a sanear o texto que entra no prompt do LLM.
- `src/services/simulation_service.py`: snapshot sem dívidas válidas agora retorna 422 `SNAPSHOT_INVALID`.

### 3. Contrato, testes e documentação

- **OpenAPI:** limites do orçamento, respostas 404 e 503, e descrição de `total_savings_vs_minimum`.
- **Teste novo:** `test_budget_below_minimums_pays_highest_cet_first`. Ele falhou uma vez por `TypeError`, porque `pytest.approx` não aceita `<=`. Uma tarefa seguinte corrigiu a asserção para uma comparação simples com `float`.
- **ADR:** `docs/adr/0001-recomendacao-por-regra-deterministica.md`. Registra que o cenário recomendado é escolhido por regra determinística (menor juros total entre os cenários que quitam) e que o Granite apenas redige a explicação.
- **Diagramas:** `docs/m3-diagrama-sequencia.md`, com dois diagramas Mermaid (`POST /simulation` e `POST /recommendation`) e os caminhos de erro.

### 4. Commits

Uma tarefa de 227 s analisou `git status` e `git diff` e criou 5 commits em Conventional Commits, com corpo explicativo, agrupados em:

1. scripts de validação na cloud;
2. ajuste do prompt do Agente 2;
3. endurecimento de segurança;
4. contrato OpenAPI;
5. documentação.

O Bob encerrou com a árvore de trabalho limpa.

## Gasto de crédito

O total foi de **5,777**, valor gravado pelo Bob em cada tarefa (o banco não informa a unidade). As revisões e a criação dos commits somam 77% do gasto.

| Categoria | Tarefas | Custo | Parcela |
| --- | --- | --- | --- |
| Revisões somente leitura | 3 | 2,410 | 42% |
| Criação dos commits | 1 | 2,023 | 35% |
| Edições pontuais (código, OpenAPI, teste) | 10 | 0,896 | 16% |
| Documentação (ADR e diagramas) | 2 | 0,379 | 7% |
| Testes de sanidade | 3 | 0,069 | 1% |

As parcelas estão arredondadas.

- **Tarefa mais cara:** a criação dos commits (2,023 em 227 s). Foram 38 respostas e 32 comandos em sequência, e cada chamada reenvia o contexto.
- **Revisões:** entre 0,504 e 1,002 cada, de 26 a 46 s e de 37 mil a 51 mil tokens de contexto.
- **Edições pontuais:** entre 0,057 e 0,184 cada, de 5 a 19 s.

## Tarefas em detalhe

Todas as tarefas são de 05/11/2026. A duração vai da primeira à última mensagem da tarefa.

| # | Hora | Modo | Tarefa | Custo | Tokens de contexto | Duração (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 10:11 | ask | Revisão de segurança do M3 | 0,504 | 37.620 | 26 |
| 2 | 10:22 | ask | Revisão de segurança do M3 (2ª rodada, escopo ampliado) | 1,002 | 45.059 | 45 |
| 3 | 10:58 | agent | `models.py`: limites de `monthly_budget` | 0,058 | 9.911 | 5 |
| 4 | 10:58 | agent | `client.py`: `asyncio.to_thread` | 0,061 | 10.744 | 6 |
| 5 | 10:58 | agent | `engine.py`: limite de horizonte e parcelas | 0,072 | 13.595 | 6 |
| 6 | 10:59 | agent | `simulation_service.py` e `.env.example`: banco `decision_traces` | 0,094 | 13.009 | 10 |
| 7 | 10:59 | agent | `store.py`: codificação de URL e `_check` | 0,184 | 15.949 | 19 |
| 8 | 11:00 | agent | `recommendation_service.py`: `_safe_text` | 0,117 | 13.813 | 11 |
| 9 | 11:02 | ask | Code review do M3 | 0,904 | 51.107 | 46 |
| 10 | 11:20 | ask | "Responda apenas: ok" | 0,004 | 2.045 | 1 |
| 11 | 11:29 | agent | Listar arquivos de `tests` | 0,031 | 7.788 | 3 |
| 12 | 11:29 | agent | Listar arquivos de `tests` | 0,034 | 8.462 | 3 |
| 13 | 11:40 | agent | `create_simulation`: validação do snapshot (422) | 0,057 | 10.337 | 5 |
| 14 | 11:40 | agent | `openapi.yaml`: limites e respostas 404/503 | 0,082 | 16.628 | 12 |
| 15 | 11:41 | agent | `test_engine.py`: novo teste de orçamento abaixo dos mínimos | 0,100 | 15.853 | 11 |
| 16 | 11:42 | agent | Correção da asserção com `pytest.approx` | 0,071 | 9.548 | 11 |
| 17 | 11:42 | agent | ADR da recomendação por regra determinística | 0,211 | 23.531 | 47 |
| 18 | 11:44 | agent | Diagramas de sequência (Mermaid) | 0,168 | 19.788 | 31 |
| 19 | 11:45 | agent | Análise do diff e 5 commits | 2,023 | 40.190 | 227 |
|  |  |  | **Total** | **5,777** |  |  |

## Ressalvas

- **Só uma sessão registrada.** O banco guarda uma única janela de trabalho (05/11, 10:11 a 11:49). Há ainda 6 tarefas vazias, sem prompt e com custo 0: 26/10, 04/11, 05/11 (3) e 11/11.
- **Unidade do custo.** O banco grava o valor de `cost` e os tokens de contexto, sem dizer a unidade nem mostrar o resumo de consumo como a IDE mostra.
- **Tempo de uso.** Não há registro de tempo ativo. As durações vêm dos horários da primeira e da última mensagem de cada tarefa.
- **Hashes dos commits.** A resposta final do Bob cita os hashes `293f18f`, `4ee70a8`, `995b225`, `567c1d7` e `d35cfd5`. Não verifiquei se eles existem no histórico atual do repositório, que pode ter sido alterado depois (por PR ou rebase). Confirme antes de citá-los.
- **Prompts e títulos.** Alguns títulos de tarefa aparecem truncados ou com acentos corrompidos no banco. Os prompts completos estão nas mensagens.

## Como os dados foram obtidos

Os números vêm do banco local do Bob Shell, em `~/.bob/db/bob.db` (SQLite). A leitura foi feita numa cópia do arquivo, sem alterar o original.

- **Tabela `tasks`:** título, status, modo, modelo e o campo `costs` (custo e tokens de contexto) de cada tarefa.
- **Tabela `messages`:** prompts, respostas do modelo, chamadas de ferramenta (nome e argumentos) e os custos por resposta.
- **Logs:** 10 arquivos em `~/.bob/logs/shell/`, de 05/11 entre 11:20 e 11:45.

Para reabrir uma tarefa e tirar o print do consumo, use `bob chat -r` (seletor de tarefas) ou `bob chat -r <id-da-tarefa>`. O comando `bob --list-tasks` falhou quando executado fora de um terminal interativo.
