# M3 — Revisão de segurança (IBM Bob) e triagem

Revisão somente leitura feita pelo IBM Bob Shell (`bob run`, modo `ask`) em 2026-11-05 sobre o código da Sub-Tarefa 3.
Cada achado foi triado por quem integra o M3; as correções abaixo foram aplicadas pelo Bob (modo `agent`) em
escopo fechado e validadas com `pytest` (20 passam) e com o smoke test na cloud real.

## Corrigido

| Achado | Correção |
|---|---|
| Traces (prompt completo + dados financeiros ligados ao `pseudonym_id`) no mesmo banco dos snapshots | `CLOUDANT_DB_DECISIONS` agora tem padrão próprio, `decision_traces`, com acesso a ser restrito por IAM do Cloudant |
| `monthly_budget` sem limite superior e inconsistência `ge=0` x motor `> 0` | `Field(gt=0, le=1_000_000)` (valor inválido vira 422) |
| `simulate()` síncrono bloqueava o event loop | `await asyncio.to_thread(simulate, ...)` |
| `remaining_installments` enorme alongava o baseline | `installments` limitado a `[1, MAX_MONTHS]` e horizonte do baseline a `MAX_MONTHS` |
| Erros HTTP do Cloudant sem tratamento | `CloudantStore._check` converte em `ServiceError(503)` |
| `db`/`doc_id` sem codificação na URL do Cloudant | `urllib.parse.quote(..., safe="")` |
| `institution`/`product_type` do snapshot entravam no prompt sem saneamento | `_safe_text` remove caracteres de controle e trunca em 40 caracteres |

## Resolvido (B4)

| Achado | Correção |
|---|---|
| LLM inventava valores R$ e afirmações de economia mesmo com `orcamento_abaixo_dos_minimos=true`; o prompt sozinho não bastava | Guarda determinística `src/agents/explanation_guard.py`: extrai e verifica cada valor R$ da explicação contra os FATOS; rejeita afirmações de economia quando o orçamento está abaixo dos mínimos. Na 1ª falha, tenta novamente (source `llm_retry`); se a 2ª também falhar, substitui por explicação determinística construída diretamente dos FATOS (source `fallback`). Nenhuma saída com issues chega ao cliente. Os campos `explanation_source` e `guard_issues` (só códigos) são gravados no trace de auditoria. |

## Limitações conhecidas (não corrigidas de propósito)

- **Sem autenticação e sem checagem de dono (IDOR) nos endpoints.** Por desenho do PLANO, OAuth2/FAPI é responsabilidade do IBM API Connect
  (ver `docs/m1-api-connect-policies.md`). O M3 não valida o token; o gateway precisa ser a única entrada. Ao ligar o gateway, vincular
  `snapshot_id`/`simulation_id` ao titular do token.
- **`REQUIRE_CONSENT=false` por padrão**, porque o `/consent` do M1 ainda não persiste consentimentos. Ligar quando o M1 estiver pronto.
- **O trace ainda guarda o prompt completo**, necessário para auditoria. A proteção é a separação de banco; criptografia em repouso e política
  de retenção ficam com a Sub-Tarefa 4 (M4).
  **Resolvido pelo M4 (Parte B):** o trace **continua guardando** o texto completo de `prompt` e `output` — necessário para auditoria e explicabilidade. Os campos `prompt_sha256`/`output_sha256` são **adicionais** e servem para verificação de integridade e para correlacionar o evento de auditoria (que carrega apenas os hashes) com o trace. Nada é descartado. A proteção do texto se dá por: banco separado (`decision_traces`), guarda de PII antes do `put` (o prompt contém apenas fatos financeiros pseudonimizados), retenção a2 e apagamento via `/internal/erase` (campo `decisions_deleted`).
- **`first_steps` envia o nome da instituição ao LLM**: informação útil ao cliente e não é dado pessoal direto, mas aumenta o potencial de
  reidentificação junto com o `pseudonym_id`. Reavaliar com o M4.
  **Resolvido pelo M4 (Parte B):** o nome da instituição no prompt é saneado por `_safe_text` (remove caracteres de controle, trunca em 40 chars) e não constitui dado pessoal direto. O trace passa pela guarda de PII antes de persistir; o texto completo de prompt e output é **mantido** (necessário para auditoria); os hashes `prompt_sha256`/`output_sha256` são adicionados para integridade e correlação com o evento de auditoria.

## Descartado

- **SSRF via `WATSONX_SIMULATION_URL`/`CLOUDANT_URL`/`WATSONX_URL`**: essas variáveis são configuradas pelo operador, não por quem chama a API.
  Risco aceito; validar HTTPS/host seria endurecimento de deploy, não correção de código.
- **"Erro HTTP do Cloudant vaza detalhes ao cliente"**: o FastAPI devolve 500 genérico; o tratamento foi feito por robustez, não por vazamento.
- **Cache de token IAM chaveado pela API key**: risco apenas com dump de memória do processo.
