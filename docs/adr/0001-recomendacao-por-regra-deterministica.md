# ADR 0001 — Recomendação de cenário por regra determinística (não pelo LLM)

**Status:** Aceito  
**Data:** 2026-11-05  
**Responsável:** M3

---

## Contexto

O PLANO.md (Sub-Tarefa 3) descrevia o Agente 2 como um `AgentExecutor` LangChain que, a partir do snapshot financeiro do cliente, chamaria a tool `run_simulation` e, depois, usaria o Granite para escolher e explicar o cenário recomendado:

> "Agente 2 recebe snapshot → chama Motor de Simulação (AI Service) → Motor retorna 3 cenários: agressivo / consolidação / portabilidade → Agente 2 gera recomendação em linguagem simples (Granite)"  
> — PLANO.md, Fluxo de Dados Principal, passo 6–8

O PLANO listava como entregável a tool `run_simulation` do Agente 2 e previa que a orquestração LangChain integraria as etapas de seleção e explicação do cenário numa cadeia encadeada.

Durante a implementação, ficou evidente que delegar ao LLM a decisão financeira (qual cenário é o melhor) traria três problemas irresolvíveis para uma aplicação bancária:

1. **Não-reprodutibilidade:** o mesmo conjunto de números pode gerar respostas diferentes entre chamadas, dependendo da temperatura e do estado interno do modelo.
2. **Risco de alucinação financeira:** o LLM pode referenciar valores de um cenário ao falar de outro, inventar taxas ou prazos não presentes nos dados, ou ignorar critérios regulatórios.
3. **Auditabilidade insuficiente para LGPD/Bacen:** decisões de crédito e endividamento precisam ser explicáveis e reproduzíveis por uma regra registrável; um raciocínio interno de LLM não é auditável.

Adicionalmente, o endpoint `POST /recommendation` foi projetado para receber uma simulação **já criada** (identificada por `simulation_id`), não um snapshot bruto. Isso tornou a cadeia `AgentExecutor → run_simulation → LLM escolhe` desnecessária no fluxo do endpoint: a simulação já existe no momento da recomendação.

---

## Decisão

**O cenário recomendado é escolhido por uma função determinística pura (`select_recommended`), e o LLM (Granite via watsonx.ai) é usado exclusivamente para redigir a explicação textual a partir dos números já calculados pelo Motor de Simulação.**

### Regra de seleção — [`select_recommended`](../src/agents/recommendation_agent.py:55)

```python
def select_recommended(scenarios: list[SimulationScenario]) -> ScenarioId:
    """Menor juros total entre os cenários que quitam as dívidas; desempate por prazo."""
    viable = [s for s in scenarios if s.total_months < MAX_MONTHS] or scenarios
    return min(viable, key=lambda s: (s.total_interest_paid, s.total_months)).scenario_id
```

- **Critério primário:** menor `total_interest_paid` entre os cenários cujo prazo de quitação é inferior a `MAX_MONTHS` (cenários que de fato quitam as dívidas).
- **Desempate:** menor `total_months` (prazo mais curto, em caso de empate no custo de juros).
- **Fallback:** se nenhum cenário quitar dentro do limite, todos são considerados candidatos e o mesmo critério se aplica.

A regra é idêntica à registrada no trace de auditoria pelo módulo [`governance.py`](../src/services/governance.py:27):

```
"selection_rule": "min(total_interest_paid), desempate por total_months, entre cenários que quitam"
```

### Cadeia LCEL — [`build_chain`](../src/agents/recommendation_agent.py:113)

A cadeia LangChain Expression Language (LCEL) construída em `build_chain` opera na seguinte sequência:

1. `RunnablePassthrough.assign(recommended=…)` — aplica `select_recommended` sobre os cenários e injeta o campo `recommended` no dicionário.
2. `RunnableLambda(with_steps)` — resolve os primeiros passos do mês 1 para o cenário recomendado.
3. `RunnablePassthrough.assign(prompt_value=…)` — serializa os fatos (números do Motor) em JSON pseudonimizado e preenche o `ChatPromptTemplate`.
4. `RunnablePassthrough.assign(prompt_text=…, explanation=…)` — materializa o prompt como string (`prompt_text`) e invoca o Granite para gerar a explicação (`explanation`).

O LLM recebe apenas dados já calculados pelo Motor via o campo `FATOS (JSON)` no prompt, e o system prompt proíbe explicitamente a invenção de valores:

> "Use SOMENTE os números fornecidos em FATOS. Nunca invente valores, taxas ou prazos."  
> — [`SYSTEM_PROMPT`](../src/agents/recommendation_agent.py:24)

### Trace de governança — [`record_trace`](../src/services/governance.py:18)

Após cada recomendação, [`recommendation_service.py`](../src/services/recommendation_service.py:71) persiste um documento de trace contendo:

- `recommended_scenario` — cenário escolhido pela regra determinística.
- `prompt` — texto completo do prompt enviado ao Granite (`prompt_text`).
- `output` — resposta literal do modelo (`explanation`).
- `model_id` — identificador do modelo utilizado.
- `selection_rule` — descrição textual da regra aplicada.
- `simulation_id`, `snapshot_id`, `pseudonym_id` — rastreabilidade até os dados de origem.

### Tool `run_simulation`

A tool [`run_simulation`](../src/agents/tools.py:14) foi implementada conforme o PLANO e permanece disponível em `AGENT2_TOOLS`. Ela pode ser usada por um futuro `AgentExecutor` ou em testes de integração que precisem criar uma simulação e obter cenários num único passo. No fluxo atual do endpoint `POST /recommendation`, contudo, a simulação é carregada do store (`simulation_id` fornecido pelo cliente) e a tool não é invocada.

---

## Alternativas consideradas

### 1. AgentExecutor com LLM escolhendo o cenário (abordagem original do PLANO)

O Agente 2 seria um `AgentExecutor` que chamaria `run_simulation` como tool e depois deixaria o Granite decidir qual cenário recomendar com base no raciocínio interno do modelo.

**Por que foi descartada:**
- A decisão financeira ficaria sujeita à variabilidade do LLM (temperatura, versão do modelo).
- O trace de auditoria registraria apenas a conclusão do modelo, não a regra aplicada — insuficiente para exigências de explicabilidade (LGPD Art. 20, Bacen).
- Risco real de o LLM cruzar valores entre cenários ao formular a resposta.

### 2. LLM recebendo os números brutos e escolhendo + explicando em uma única chamada

Uma única chamada ao Granite com todos os números, pedindo tanto a escolha quanto a explicação.

**Por que foi descartada:**
- Mesmos problemas de reprodutibilidade e auditabilidade da alternativa 1.
- Dificulta separar, no trace, qual parte da saída é "decisão" e qual é "explicação".

### 3. Regra determinística sem LLM (somente o número calculado, sem texto)

Retornar apenas o `recommended_scenario` e os dados numéricos, sem explicação em linguagem natural.

**Por que foi descartada:**
- O produto exige que o cliente receba uma explicação acessível (níveis `simple` e `technical`).
- A geração de texto adaptado ao nível de linguagem é exatamente o valor que o Granite agrega sem risco financeiro, pois não tem acesso a dados além dos `FATOS` fornecidos.

---

## Consequências

### Positivas

- **Reprodutibilidade total da decisão financeira:** dados idênticos sempre produzem o mesmo `recommended_scenario`, independentemente do estado do LLM.
- **Auditabilidade completa:** a `selection_rule` é uma string literal registrada em cada trace; qualquer auditor pode verificar que a regra foi aplicada corretamente inspecionando `total_interest_paid` e `total_months` dos cenários armazenados.
- **O LLM não tem acesso a dados além do necessário:** o campo `FATOS` contém apenas números agregados por cenário (sem CPF, saldo de conta, histórico de transações), reduzindo o risco de exposição de dados pessoais.
- **Prompt e saída do LLM são gravados integralmente:** o trace permite reproduzir exatamente o que o modelo recebeu e respondeu, atendendo ao princípio de explicabilidade da LGPD e às futuras exigências de AI Factsheets do watsonx.governance.
- **Cadeia LCEL testável de forma isolada:** `select_recommended` é uma função pura que pode ser testada sem chamar o LLM; a cadeia completa pode ser testada substituindo o LLM por um mock.

### Negativas

- **Divergência em relação ao PLANO.md:** o PLANO descrevia um `AgentExecutor` onde o LLM orquestraria a escolha. Esta decisão reduz o papel do LLM à geração de texto, o que deve ser refletido na documentação do projeto (registrado na nota de desvio da Sub-Tarefa 3).
- **Flexibilidade de critério limitada:** alterar o critério de recomendação (ex.: incluir score de risco, preferência do cliente por prazo vs. juros) exige mudança de código em `select_recommended`, não apenas de prompt. Isso é intencional, mas implica ciclo de desenvolvimento para qualquer mudança de regra.
- **Tool `run_simulation` não é usada no fluxo principal:** a tool existe e está registrada em `AGENT2_TOOLS`, mas o endpoint `POST /recommendation` parte de uma simulação já criada — a tool só seria exercitada num futuro `AgentExecutor` ou em testes específicos.
- **Granite não vê o contexto completo do cliente:** ao receber apenas os fatos agregados por cenário, o Granite não pode personalizar a explicação com base em características individuais (ex.: histórico de inadimplência). Isso é uma restrição intencional de privacidade, mas limita a personalização da narrativa.

---

## Relação com o PLANO.md

| Aspecto | PLANO.md (Sub-Tarefa 3) | Implementação real |
|---|---|---|
| Papel do Agente 2 | `AgentExecutor` que chama `run_simulation` e usa LLM para escolher + explicar o cenário | Cadeia LCEL: regra determinística escolhe; LLM só explica |
| Tool `run_simulation` | Tool principal do Agente 2; chamada a cada recomendação | Implementada em `src/agents/tools.py`; não invocada no fluxo de `POST /recommendation` (simulação já existe) |
| Orquestração | LangChain AgentExecutor (loop de raciocínio do LLM) | LangChain LCEL (cadeia linear sem loop de raciocínio) |
| Decisão financeira | LLM escolhe o cenário | Função `select_recommended` (determinística, sem LLM) |
| Trace de governança | watsonx.governance (AI Factsheets) | `record_trace` persiste no store local; envio ao watsonx.governance é próximo passo declarado em `governance.py` |

O desvio está declarado na nota da Sub-Tarefa 3 do PLANO.md:

> "Desvios: o cenário recomendado é escolhido por regra determinística (o LLM só explica); o deploy do AI Service no watsonx.ai e o envio do trace ao watsonx.governance ainda não foram executados."
