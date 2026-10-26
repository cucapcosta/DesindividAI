# DesendividAÍ

> Assistente de inteligência financeira para desendividamento, construído sobre o ecossistema IBM watsonx.

---

## Visão Geral

O **DesendividAÍ** é uma solução de IA multiagente voltada a clientes do Banco do Brasil que enfrentam dívidas em múltiplos credores. O sistema agrega dados do Open Finance Brasil, analisa o Custo Efetivo Total (CET) de cada dívida, simula estratégias de quitação/portabilidade e negocia condições diretamente com plataformas parceiras — tudo com linguagem acessível, explicabilidade auditável e conformidade com LGPD e Bacen.

---

## Arquitetura da Solução

### Camadas Lógicas

```
┌────────────────────────────────────────────────────────────────┐
│              Camada de Engajamento e Orquestração              │
│  watsonx Assistant  ·  Agente Orquestrador  ·  watsonx.ai LLM  │
│  Agente Analista  ·  Agente Conselheiro  ·  Agente Negociador  │
└───────────────────────────┬────────────────────────────────────┘
                            │ APIs seguras (mTLS / OAuth 2.0)
┌───────────────────────────▼────────────────────────────────────┐
│             Camada de Integração e Dados                       │
│     IBM API Connect + DataPower Gateway  ·  watsonx.data       │
│         Open Finance Brasil  ·  Serasa  ·  Core BB             │
└───────────────────────────┬────────────────────────────────────┘
                            │ Metadados de decisão
┌───────────────────────────▼────────────────────────────────────┐
│           Camada de Governança e Conformidade                  │
│         watsonx.governance  ·  LGPD/Bacen  ·  Auditorias      │
└────────────────────────────────────────────────────────────────┘
```

---

## Componentes IBM

### Camada de Engajamento e Orquestração

| Componente | Função |
|---|---|
| **watsonx Assistant** | Interface conversacional nativa integrada ao App BB, WhatsApp (via Twilio) e Internet Banking. Coleta consentimento inicial e gerencia o fluxo de diálogo. |
| **watsonx.ai (Foundation Models)** | Hospeda os LLMs que alimentam os quatro agentes especializados. Suporta tool calling via REST, Python SDK e LangChain. |
| **Agente Orquestrador** | Interpreta a intenção do cliente e aciona os sub-agentes corretos via framework multiagente (ex.: AutoGen + watsonx). |
| **Agente Analista Financeiro** | Ingere dados brutos e prioriza dívidas matematicamente com base no CET. |
| **Agente Conselheiro** | Traduz jargões e cenários de simulação para linguagem empática e simples. |
| **Agente Negociador** | Avalia margens pré-aprovadas e interage via API com plataformas de acordo para buscar descontos. |

### Camada de Integração e Dados

| Componente | Função |
|---|---|
| **IBM API Connect + DataPower** | Gateway de APIs para Open Finance Brasil, Serasa e plataformas de renegociação. Gerencia autenticação mútua (mTLS), rate limiting por catálogo/gateway e criptografia de ponta a ponta. |
| **watsonx.data (Lakehouse)** | Recebe, unifica e padroniza o fluxo de dados estruturados do Open Finance, Serasa e histórico interno do BB. Formatos suportados: CSV, Parquet, JSON. Engines: Presto, Spark. |

### Camada de Governança e Conformidade

| Componente | Função |
|---|---|
| **watsonx.governance** | Monitora continuamente os modelos para ausência de viés, gera documentação de explicabilidade para cada recomendação e garante aderência à LGPD e regulações do Bacen. Suporta integração com IBM OpenPages para console de governança. |

---

## Jornada do Usuário (Fluxo de Dados)

```
1. Onboarding e Consentimento
   └── App BB → API Connect → registro explícito LGPD + Open Finance

2. Agregação (Data Ingestion)
   └── Open Finance / Serasa → API Connect → watsonx.data Lakehouse

3. Análise e Simulação
   └── watsonx.data → Agente Analista (watsonx.ai) → CET, simulações

4. Recomendação Explicável
   └── Agente Conselheiro → plano de ação neutro
   └── watsonx.governance → justificativa registrada por decisão

5. Ação e Acompanhamento
   └── Agente Negociador → API Connect → parceiros de renegociação
   └── Alertas proativos mensais via watsonx Assistant
```

---

## Papel do IBM Bob no Ciclo de Vida

| Fase | Contribuição |
|---|---|
| **Arquitetura** | Diagramas de sequência do sistema multiagente; mapeamento de fluxos de autenticação mútua (mTLS) para o ecossistema Open Finance. |
| **Código e Integração** | Geração de scripts Python/Node.js para consumo das APIs do Serasa e Open Finance via API Connect; motor de simulação (CET e amortização); testes unitários automatizados. |
| **Segurança e Conformidade** | Revisão de prompts e código-fonte contra injeção de prompt; análise das rotinas de ofuscação e anonimização de dados entre watsonx.data e watsonx.ai; aderência à LGPD. |
| **Documentação Contínua** | ADRs, esquemas OpenAPI (Swagger), mapas de fluxo de dados — mantidos em sincronia com o código produzido. |

---

## Stack de Desenvolvimento

- **Linguagens**: Python 3.11+, Node.js 20+
- **SDKs**: `ibm-watsonx-ai`, `ibm-watsonx-gov`, IBM API Connect CLI (`apicup`)
- **Frameworks de agente**: AutoGen + watsonx.ai client
- **Formatos de dados**: Apache Iceberg / Parquet (watsonx.data), OpenAPI 3.x
- **Segurança**: mTLS entre subsistemas API Connect/DataPower; OAuth 2.0 para parceiros externos; IBM Cloud IAM para serviços internos

---

## Conformidade e Privacidade

- **LGPD**: Consentimento explícito coletado e registrado no API Connect antes de qualquer acesso a dados.
- **Open Finance Brasil**: Integração via API Connect respeitando os padrões do Bacen.
- **Sigilo Bancário**: Dados anonimizados no trânsito entre watsonx.data e os modelos do watsonx.ai; PII monitorada pelo watsonx.governance.
- **Auditoria**: Cada recomendação do agente tem justificativa persistida pelo watsonx.governance para auditorias regulatórias.
