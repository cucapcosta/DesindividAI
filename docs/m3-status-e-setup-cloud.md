# M3 + Cloud — Status e retomada

Atualizado em 2026-11-04. Serve para retomar o trabalho em outra máquina. **Nenhum segredo aqui**;
o `.env` não vai para o git e precisa ser recriado (ver "Retomar em outra máquina").

## 0. Validação na cloud

Decisão de 2026-11-05: **não testar chamadas de cloud na máquina/conta do autor do M3**. O código é entregue testado
apenas offline (`pytest`: 17 passam, 1 pulado). A validação contra serviços reais acontece na cloud do colega ou no merge.

**Estado de verificação (2026-11-05):** `python -m scripts.cloud_smoke_test` passou **inteiro** na conta do autor
(IAM, Cloudant, Granite `ibm/granite-4-h-small`, `/simulation`, `/recommendation` em `simple` e `technical`, trace persistido).
Ainda **não** verificado: `WATSONX_SIMULATION_URL` (AI Service não deployado) e o envio ao watsonx.governance.
A validação na conta do colega continua útil para confirmar que a configuração é reproduzível.

Observações do teste real: `granite-3-8b-instruct` não existe mais (usar `granite-4-h-small`); o parâmetro de chat é
`max_tokens` (não `max_new_tokens`); o snapshot de exemplo tem um cartão cujo mínimo não cobre os juros, então o
"juros pagando só o mínimo" do baseline fica muito alto (efeito dos dados de exemplo, não do motor).

**Como validar (uma pessoa com a cloud configurada)**
```bash
cp .env.example .env     # preencher: IBM_CLOUD_API_KEY (44 caracteres), WATSONX_URL, WATSONX_PROJECT_ID (ou WATSONX_SPACE_ID),
                         # WATSONX_MODEL_ID, CLOUDANT_URL, CLOUDANT_APIKEY
# pré-requisito: o WML precisa estar associado ao projeto (ou usar um espaço de implementação)
python -m scripts.cloud_smoke_test          # cria databases + snapshot de exemplo e testa o fluxo completo
# ou, via pytest:  RUN_CLOUD_TESTS=1 pytest tests/test_cloud_integration.py -s
```
O smoke test imprime `[OK]`/`[FALHA]` por etapa (IAM, Cloudant, Granite, `/simulation`, `/recommendation` em `simple` e
`technical`, trace persistido), mostra a explicação gerada e sai com código 1 se algo falhar. **Escreve** no Cloudant
da conta usada (databases, snapshot de exemplo, simulações e traces). Só preparar os dados: `python -m scripts.cloud_setup`.

Se falhar, os erros mais prováveis: `403 no_associated_service_instance_error` (associar o WML), 404 de modelo
(trocar `WATSONX_MODEL_ID` por um Granite disponível na região), 401 com `Failed to verify user profile` (abrir o
Watson Studio uma vez pelo navegador).

## 1. M3: o que está pronto

| Item | Onde | Estado |
|---|---|---|
| Motor de Simulação (3 cenários) | `src/simulation/engine.py` | Pronto, testado |
| Cliente do Motor (AI Service ou local) | `src/simulation/client.py`, `ai_service.py` | Pronto; modo local funciona, AI Service **não deployado** |
| `POST /simulation` | `src/api/routers/simulation.py`, `src/services/simulation_service.py` | Pronto |
| Agente 2 (LCEL + Granite) | `src/agents/recommendation_agent.py`, `tools.py` | Pronto, testado com LLM falso; validado com Granite real em 2026-11-05 |
| `POST /recommendation` | `src/api/routers/recommendation.py`, `src/services/recommendation_service.py` | Pronto, mesmo caveat |
| Trace de auditoria | `src/services/governance.py` | Grava no Cloudant/memória; **sem envio ao watsonx.governance** |
| Armazenamento | `src/storage/store.py` | Cloudant (httpx + IAM); sem credenciais falha com 503, memória só com `STORE_BACKEND=memory` |
| Testes | `tests/` | 17 passando, 1 pulado (integração cloud, `RUN_CLOUD_TESTS=1`) |
| Validação cloud | `scripts/cloud_setup.py`, `scripts/cloud_smoke_test.py` | Prontos e **executados com sucesso** |
| Docs | `docs/m3-simulation-engine.md`, `docs/m3-agent2-recommendation.md` | Prontos |

Decisões: cenário recomendado por regra determinística (o LLM só explica); linha BB de consolidação é
premissa (`BB_CONSOLIDATION_CET_ANNUAL`, 30% a.a.); `REQUIRE_CONSENT=true` por padrão desde o M1 (`feat/m1-consentimento`);
modelo padrão trocado para `ibm/granite-4-h-small` (o `granite-3-8b-instruct` não existe mais na conta).

## 2. Cloud: o que já foi feito

- Conta IBM Cloud **`watsonx`** (`beed59fa4da941e9b58f117363c8b6c1`), usuário `igor.oliveira@sou.inteli.edu.br`.
  **Cada participante do evento parece ter a própria conta `watsonx`** (o ID em `infra-setup.md`, `63520643...`, é de outra).
  O time ainda precisa escolher UMA conta para integrar M2 e M3 (o snapshot precisa estar no mesmo Cloudant).
- Acesso aos serviços vem do grupo de acesso `watsonx-Hackathon-Access`. Serviços existentes: COS, Cloudant, GOV,
  Orchestrate, STT, TTS, WML, WS.
- Service ID `desendividai-agents` criado (e a chave `desendividai-agents-key`). Política **Cloudant (Manager, Writer): criada**.
  Política **WML (`pm-20`): negada** (`insufficient_permissions`: o grupo permite usar, não conceder).
- Entrei no watsonx.ai Studio (cria o perfil) e criei o projeto **`desendividai`**, ID `9f4e94bd-ec95-4f50-9e2f-bf0ed88b86a1`.
- A API key usada para desenvolvimento é uma **chave pessoal** (`ibmcloud iam api-key-create`), válida no IAM.
  Chave de 35 caracteres era inválida; uma chave IAM correta tem 44.

## 3. Cloud: o que falta (em ordem)

1. **Associar o WML ao projeto** (bloqueia o Granite). Hoje: `403 no_associated_service_instance_error`.
   No projeto: Manage → Services & integrations → Associate service. O diálogo veio **vazio**. Tentar:
   a. limpar filtros "Grupos de recursos" e "Locais" (WML está em us-south, grupo `77f0b811...`);
   b. "Novo serviço +" e ver se cria um watsonx.ai Runtime (Lite);
   c. **plano B:** criar um **Espaço de implementação** (pede a instância WML na criação), pegar o Space ID e
      adaptar o código para `space_id` (ChatWatsonx aceita). O espaço também é necessário para o deploy do AI Service.
2. Ajustar o `.env`: `WATSONX_PROJECT_ID=9f4e94bd-ec95-4f50-9e2f-bf0ed88b86a1` e `WATSONX_MODEL_ID=ibm/granite-4-h-small`.
3. **Criar os bancos do Cloudant** `financial_snapshots` e `consent_registry` (hoje respondem 404; a autenticação no Cloudant funciona).
4. Inserir um **snapshot de exemplo** (as dívidas de `tests/conftest.py`) em `financial_snapshots`, `_id = snapshot_id`.
5. (Opcional) política WML para o Service ID, se alguém com permissão puder; até lá usar a chave pessoal.

## 4. M3: o que falta (depois da cloud)

1. Rodar `/simulation` → `/recommendation` de ponta a ponta com Granite e Cloudant reais; ajustar o prompt
   (tamanho, formato R$, tom) conforme a saída.
2. **Deploy do Motor como AI Service** no watsonx.ai (precisa de espaço de implementação) e preencher `WATSONX_SIMULATION_URL`.
3. **Enviar o trace ao watsonx.governance** (AI Factsheets, instância `GOV`).
4. Mais testes de borda do motor (orçamento menor que os mínimos, uma dívida só).
5. Dependências de outros membros: M2 gravar snapshot no formato de `docs/m3-simulation-engine.md`;
   M1 persistir consentimentos para ligar `REQUIRE_CONSENT=true` — ✅ feito em `feat/m1-consentimento` (ligado por padrão).
6. ~~Opcional, código do M1: trocar `Field(example=...)` por `json_schema_extra` em `src/api/models.py`~~ — ✅ resolvido em `feat/m1-consentimento`.

## 5. Retomar em outra máquina

```bash
git clone <repo> && cd desendividai
git checkout feat/m3-simulacao-recomendacao
python -m venv .venv                 # Python 3.11+ (foi usado 3.12)
.venv/Scripts/python.exe -m pip install -r requirements.txt     # Windows; em Linux/Mac: .venv/bin/python
cp .env.example .env                 # preencher valores
.venv/Scripts/python.exe -m pytest -q                            # esperado: 17 passed, 1 skipped
uvicorn src.main:app --reload        # a partir da raiz; Swagger em /docs
```

`.env`: não vai para o git. Preencher a partir do `.env.example` com as credenciais da conta escolhida
(chave IAM de 44 caracteres, URLs do watsonx.ai e Cloudant, Project ID). Comandos úteis no Git Bash (barras normais!):

```bash
ibmcloud login --sso -r us-south
ibmcloud target                      # confirmar a conta `watsonx`
ibmcloud iam api-key-create <nome> -d "dev"      # o segredo só aparece uma vez
```

Conferir o tamanho da chave sem expô-la:
`.venv/Scripts/python.exe -c "from dotenv import dotenv_values; print(len(dotenv_values('.env').get('IBM_CLOUD_API_KEY') or ''))"` (esperado: 44).

Armadilhas já encontradas: arquivo `.env` não salvo no editor; `load_dotenv` não sobrescreve variável já definida
no ambiente do sistema; comandos `ibmcloud` precisam do prefixo completo; o Studio só cria o perfil no primeiro acesso
pelo navegador (`dataplatform.cloud.ibm.com`).
