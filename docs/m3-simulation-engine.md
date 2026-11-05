# M3 — Motor de Simulação

Implementação: `src/simulation/engine.py` (lógica pura, só stdlib), `ai_service.py` (empacotamento
watsonx.ai), `client.py` (chama o AI Service ou roda local), `src/services/simulation_service.py`
(caso de uso de `POST /simulation`).

## Interface Agente 1 → Agente 2 (snapshot)

O Agente 1 (M2) persiste em `CLOUDANT_DB_SNAPSHOTS` um documento com `_id = snapshot_id`:

```json
{
  "_id": "<snapshot_id uuid>",
  "pseudonym_id": "a3f9c1...",
  "total_outstanding": 25000.0,
  "generated_at": "ISO-8601",
  "debts": [ { "debt_id", "institution", "product_type", "outstanding_balance",
               "cet_annual", "monthly_payment", "remaining_installments", "priority_rank" } ]
}
```

`debts[]` é exatamente o schema `Debt` do OpenAPI. Nenhum CPF entra aqui.

## Contrato do AI Service

```
POST <WATSONX_SIMULATION_URL>/predict
{ "debts": [Debt...], "monthly_budget": 1500.0, "bb_cet_annual": 30.0 }   # bb_cet_annual opcional
→ { "scenarios": [SimulationScenario x3] }                                 # aggressive, consolidation, minimum_viable
→ erro de entrada: { "code": "INVALID_INPUT", "message": "..." }
```

Sem `WATSONX_SIMULATION_URL`, o mesmo código roda dentro da API (modo local).

### Deploy no watsonx.ai
1. Empacotar `src/simulation/engine.py` como package extension (ou concatená-lo ao arquivo da função).
2. Registrar `deployable_ai_service` (`ai_service.py`) com `client.repository.AIServices.store(...)`.
3. `client.deployments.create(...)` e copiar o endpoint para `WATSONX_SIMULATION_URL`.

> O deploy em si depende da conta do hackathon e não foi executado neste repositório.

## Cenários

| Cenário | Regra |
|---|---|
| `aggressive` | Mínimos de todas as dívidas; todo o orçamento restante na dívida de maior CET (avalanche). |
| `consolidation` | Dívidas com CET > linha BB viram um único financiamento (PMT price, prazo = maior prazo restante). Excedente vai para a dívida de maior CET. Se nenhuma dívida passa no corte, equivale ao agressivo (a descrição avisa). |
| `minimum_viable` | Mínimos de todas; excedente só na dívida mais cara. Quando ela quita, o excedente deixa de ser usado. |

Se o orçamento não cobre os mínimos, estes são pagos em ordem decrescente de CET até acabar.

## Premissas

- Juros mensais = (1 + CET a.a.)^(1/12) − 1, aplicados antes do pagamento do mês.
- Linha BB de consolidação: `BB_CONSOLIDATION_CET_ANNUAL` (padrão 30% a.a.). **É premissa, não oferta real.** Sem tarifas de portabilidade.
- `total_savings_vs_minimum` = juros pagando só o mínimo contratado − juros do cenário, ambos no mesmo horizonte (maior entre o prazo contratado e o do cenário mais longo), pois dívidas cujo mínimo não cobre os juros (ex.: rotativo) nunca quitam.
- Teto de 600 meses; se não quita, a descrição avisa ("não quita em 50 anos").
- **Dívidas que não amortizam no mínimo** (pagamento ausente ou abaixo dos juros do mês: dívida só do Serasa
  com `monthly_payment = 0`, rotativo com `remaining_installments = 0`, cheque especial cujo mínimo é o juro
  contratual e fica abaixo do CET com IOF): o motor assume um pagamento fixo de **juros iniciais + 1% do saldo**
  (`ASSUMED_MIN_PRINCIPAL_PCT`) e declara a premissa ("Premissa: N dívida(s)…") na descrição de cada cenário.
  Prazo ausente (`remaining_installments <= 0`) vira 36 meses (`DEFAULT_TERM_MONTHS`) — afeta o prazo da
  linha BB na consolidação. O piso de 1% é uma premissa do motor, não um dado do banco.
- **Proteção contra divergência:** se o saldo total passa de 10× o inicial (orçamento abaixo dos juros), a
  simulação é interrompida e o cenário é marcado "não quita — … interrompida no mês N". Os valores nunca explodem.
- Dívida malformada no snapshot (campo ausente, não numérico, CET negativo) → `400 INVALID_INPUT`, nunca 500.
- A dívida consolidada usa o `debt_id` fixo `uuid5("desendividai:bb-consolidation")` (`CONSOLIDATED_DEBT_ID`).

## Persistência

`POST /simulation` grava `simulation:<simulation_id>` em `CLOUDANT_DB_DECISIONS` (padrão: DB dos snapshots)
com `type: "simulation"`, `pseudonym_id`, `monthly_budget` e a resposta completa.

## Erros

| Status | code | Quando |
|---|---|---|
| 400 | `INVALID_INPUT` | orçamento ≤ 0, sem dívida em aberto ou dívida malformada no snapshot |
| 403 | `CONSENT_REQUIRED` | `REQUIRE_CONSENT=true` e sem consentimento `AUTHORISED` |
| 404 | `SNAPSHOT_NOT_FOUND` | snapshot inexistente |
| 503 | `SERVICE_UNAVAILABLE` | AI Service, IAM ou Cloudant indisponível |
