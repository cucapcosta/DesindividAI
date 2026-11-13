# Entrega final — DesendividAÍ

Os três textos exigidos pelo guia do AI Experiential Learning Lab (seção 7, "Requisitos do Projeto"): o problema e a solução, o link do protótipo e a tecnologia. Os outros dois entregáveis estão no repositório: o vídeo em [`docs/video/desendividai-demo.mp4`](video/desendividai-demo.mp4) e as sessões do IBM Bob em [`bob_sessions/`](../bob_sessions/).

| Entregável | Onde |
|---|---|
| Vídeo de demonstração (3 min) | [`docs/video/desendividai-demo.mp4`](video/desendividai-demo.mp4) |
| Problema e solução | [abaixo](#problema-e-solução) |
| Protótipo funcional | <https://github.com/cucapcosta/DesindividAI> — ver [Protótipo](#protótipo-funcional) |
| Tecnologia | [abaixo](#tecnologia) |
| Sessões do IBM Bob | [`bob_sessions/`](../bob_sessions/) |

---

## Problema e solução

### Problema

Quem deve a vários credores ao mesmo tempo (cartão, cheque especial, crédito pessoal, financiamento) costuma não saber qual dívida custa mais. A taxa nominal esconde tarifas, seguros e IOF, e quem compara as dívidas pelo valor da parcela, e não pelo Custo Efetivo Total (CET), acaba pagando primeiro as dívidas mais baratas enquanto as mais caras crescem. As informações estão espalhadas entre bancos e birôs de crédito, e uma renegociação feita sem esse quadro completo muitas vezes troca uma dívida cara por outra.

### Usuário-alvo

Clientes pessoa física do Banco do Brasil endividados com mais de uma instituição, que querem um plano para sair das dívidas cabendo no orçamento do mês e não têm familiaridade com termos como CET, amortização e portabilidade.

### Solução

O DesendividAÍ é um assistente multiagente sobre o IBM watsonx que:

1. **Coleta com consentimento.** O cliente autoriza, pelo fluxo de consentimento do Open Finance, quais instituições e quais escopos o sistema pode ler. O consentimento pode ser revogado a qualquer momento e, a partir daí, as rotas protegidas passam a responder `403`.
2. **Consolida e prioriza pelo CET.** O Agente 1 junta os contratos do Open Finance com o relatório do Serasa, estima o CET das dívidas que só aparecem no Serasa a partir das taxas médias do Banco Central e ordena as dívidas pela mais cara.
3. **Simula cenários.** O motor de simulação calcula três cenários, cada um com plano mês a mês: quitação agressiva, consolidação/portabilidade para o BB e mínimo viável.
4. **Recomenda em linguagem simples.** Uma regra determinística e auditável escolhe o cenário ([ADR 0001](adr/0001-recomendacao-por-regra-deterministica.md)), e o Agente 2 (Granite) explica a escolha sem jargão. Uma guarda bloqueia qualquer número na explicação que não venha da simulação.
5. **Protege os dados (LGPD).** O CPF vira pseudônimo HMAC antes de chegar aos agentes, o CPF original fica num cofre cifrado com AES-256-GCM, uma guarda impede PII no prompt e na auditoria, e cada recomendação deixa um trace auditável. O titular pode pedir acesso e apagamento dos dados (rotas internas `/internal/*`).

O que a visão de produto prevê e ainda não está implementado (Agente Negociador, watsonx Orchestrate, watsonx.governance, alertas mensais) está na tabela [Visão × implementado](../README.md#visão--implementado) do README, com o bloqueio de cada item.

---

## Protótipo funcional

**Link:** <https://github.com/cucapcosta/DesindividAI>

O protótipo é a API FastAPI com a página de demonstração `/demo`, e roda localmente seguindo o [Como rodar](../README.md#como-rodar) do README. O vídeo mostra o fluxo completo funcionando, de ponta a ponta, contra os serviços reais da IBM Cloud.

**Por que não há URL pública:**

- **Code Engine:** o grupo de acesso do hackathon (`watsonx-Hackathon-Access`) tem só o papel de plataforma `Viewer` e não consegue criar projeto.
- **Hospedagem fora da conta:** hospedar fora da conta do hackathon exporia na internet a API key pessoal de um integrante e o Cloudant compartilhado do time.

---

## Tecnologia

| Camada | Tecnologia | Uso |
|---|---|---|
| LLM | IBM watsonx.ai, `ibm/granite-4-h-small` | Agente 1 (coleta e priorização, `AgentExecutor` do LangChain com tools) e Agente 2 (explicação da recomendação, LCEL) |
| Motor de simulação | Python, empacotado como AI Service do watsonx.ai ([`ai_service.py`](../src/simulation/ai_service.py)) | Três cenários com plano de amortização mês a mês; roda localmente enquanto `WATSONX_SIMULATION_URL` estiver vazio |
| API | FastAPI, contrato OpenAPI 3.0 ([`openapi/openapi.yaml`](../openapi/openapi.yaml)) | `/consent` → `/debts` → `/simulation` → `/recommendation`, mais as rotas internas `/internal/*` de direitos do titular (acesso e apagamento, protegidas por token) |
| Dados | IBM Cloudant | Consentimentos, snapshots financeiros, traces de decisão, cofre de identidade e trilha de auditoria |
| Auditoria | IBM Cloud Object Storage | Cópia da trilha de auditoria |
| Autenticação | IBM Cloud IAM | Tokens para watsonx.ai, Cloudant e COS |
| Privacidade | HMAC-SHA256, AES-256-GCM com envelope encryption | Pseudonimização, cofre de CPF, guarda de PII, retenção e apagamento ([`src/privacy/`](../src/privacy/)) |
| Dados externos | API SGS do Banco Central (real); Open Finance e Serasa (sandbox) | Taxas médias de mercado; contratos e relatório de dívidas fictícios |
| Desenvolvimento | IBM Bob IDE | Implementação, testes, revisão e documentação ([`bob_sessions/`](../bob_sessions/)) |

### Decisões de projeto

- **Por que a escolha do cenário fica numa regra:** o LLM explica mas não escolhe. A escolha do cenário é uma regra determinística, e uma recomendação financeira precisa ser reproduzível e auditável. O Granite só redige a explicação, e a guarda de números impede que ele invente valores.
- **Cloudant no lugar do watsonx.data:** para um protótipo com poucos documentos por cliente, um banco de documentos basta. O lakehouse fica na visão de produto.
- **O que ficou só especificado:** o gateway (IBM API Connect com FAPI e mTLS) está especificado no contrato OpenAPI e no [`PLANO.md`](../PLANO.md), mas o serviço não existe na conta.
