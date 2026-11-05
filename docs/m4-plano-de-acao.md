# M4 — Plano de Ação, Parte A: Dados, Pseudonimização, Retenção e Auditoria sobre o Agente 1 (M2)

> **Para o agente executor (IBM Bob).** Este documento é a especificação da **Parte A** da
> Sub-Tarefa 4 do `PLANO.md`. Execute as fases **na ordem**, rodando `pytest` ao fim de cada uma,
> e **pare para revisão** ao fim da Fase 0. No final, entregue a "Definição de Pronto" preenchida.
>
> **Base:** branch `develop`, com M1 (documentação + contrato OpenAPI) e M2 (Agente 1, `GET /debts`).
> **O M3 (simulação, recomendação, `src/core`, `src/storage`, `src/services`) ainda NÃO está na base.**
> Não crie, não importe e não simule nada do M3. A integração com o M3 é a **Parte B**, que virá em
> outro prompt (ver o fim deste documento).

---

## 1. Estado atual da base (levantado em 05/11/2026; confirme na Fase 0)

| Origem | Artefato | O que significa para o M4 |
|---|---|---|
| M1 | `docs/m1-consent-lifecycle.md` | Schema do `consent_registry`, TTLs, eventos auditáveis e fluxo `POST /internal/erase/{pseudonym_id}` (o passo 3c delega ao M4). Pressupõe **TTL do MongoDB**, que o Cloudant **não tem** |
| M1 | `src/api/routers/consent.py` | `POST /consent` e `DELETE /consent/{id}` ainda são **stubs**. Hoje os consentimentos de teste vêm de `scripts/seed_test_consent.py` |
| M2 | `src/privacy/pseudonymization.py` | **Já existe**: `normalize_cpf`, `pseudonymize_cpf` (prefixo `cpf:`), `pseudonymize_contract` (prefixo `contract:`), chave `PSEUDONYM_HMAC_KEY` e chave efêmera em dev. **O M4 é dono deste módulo**: estender, sem reescrever e sem mudar a saída |
| M2 | `src/agents/agent1/repository.py` | Camada de Cloudant da base: SDK `ibmcloudant` **síncrono** + `IAMAuthenticator`, com `InMemoryRepository` para testes. **O M4 segue o mesmo padrão** (sem criar client HTTP próprio) |
| M2 | `src/agents/agent1/tools.py` | `persist_snapshot` grava o snapshot com `expires_at = expires_at do consentimento + 90 dias` (`SNAPSHOT_RETENTION_AFTER_CONSENT`). `agent.tool_calls` registra a trilha das tools |
| M2 | `src/agents/agent1/sources.py` | `HttpSerasaClient.fetch_report(pseudonym_id)` assume uma **camada de re-identificação do M4** que resolve o CPF |
| M2 | `docs/m2-agent1.md` §7, §8 e §12 | Schema do snapshot (sem CPF, nome, nº de contrato ou score exato); índices `by-pseudonym` e `by-expiry` **já criados**; pendências atribuídas ao M4 |
| M2 | `tests/conftest.py` | Zera as credenciais reais e fixa `PSEUDONYM_HMAC_KEY` de teste. Os testes **nunca** chamam a rede |

### Pendências do M4 listadas pelo M2 (`docs/m2-agent1.md` §12) que entram na Parte A

1. Confirmar a retenção de 90 dias e o dono da chave de pseudonimização
2. `PSEUDONYM_HMAC_KEY` vem do `.env` (gestão da chave)
3. Serviço de re-identificação para consultar o Serasa por CPF
4. Expurgo de snapshots por `expires_at` (índice `by-expiry` pronto)
5. Trilha do Agente 1 (`agent.tool_calls`) registrada para auditoria e governança

*(As pendências "separar `financial_snapshots` de simulações/traces" e "reaproveitar `services/governance.py`" dependem do M3 e ficam para a Parte B.)*

### Infra (verificada)

| Item | Estado |
|---|---|
| Cloudant | IAM OK; `financial_snapshots` e `consent_registry` existem (índices do M2 criados) |
| COS | HMAC OK; bucket `desendividai-artifacts` (us-south) existe |
| watsonx.ai | WML associado; `ibm/granite-4-h-small` validado pelo M2 em 05/11 |
| Secrets Manager / HSM | **Indisponíveis** na conta do hackathon |

---

## 2. Decisões de arquitetura do M4 (seguir)

1. **Compatibilidade.** `pseudonymize_cpf()` e `pseudonymize_contract()` mantêm **a mesma assinatura e a mesma saída** com a chave atual. Mudar isso quebra consentimentos, snapshots e `debt_id` já gravados. A versão da chave vai em um campo separado (`key_version`) nos documentos novos.
2. **Retenção sem TTL.** O Cloudant não expira documentos. A política de TTL do M1 vira um **job de expurgo** idempotente sobre `expires_at` / `awaiting_expires_at`, usando índices Mango.
3. **HMAC é irreversível.** O `pseudonym_id` serve só para correlação. A re-identificação (Serasa, obrigações legais) usa um **cofre de identidade** separado: CPF cifrado com AES-256-GCM e **uma DEK por titular**, envolvida pela KEK (*envelope encryption*).
4. **Esquecimento = crypto-shredding.** Apagar a DEK do titular torna irrecuperável qualquer cópia do CPF, inclusive em backups.
5. **Chaves.** No hackathon, as chaves vêm do ambiente, atrás da interface `KeyProvider`. A produção (`SecretsManagerKeyProvider`: Secrets Manager + HSM, Trusted Profile) fica **especificada**, com esqueleto. **Nunca** guardar chave no COS. Corrigir os textos que dizem "COS + envelope encryption" (`pseudonymization.py`, `docs/m2-agent1.md` §8, `PLANO.md`, `docs/infra-setup.md`).
6. **Bancos do M4:** `identity_vault` (CPF cifrado + DEK; mantido até o shredding) e `audit_trail` (eventos e traces, só com pseudônimos; **nunca** expurgado).
7. **Nenhum dado pessoal em claro** em snapshots, auditoria, logs ou COS.

---

## 3. Regras para o executor

- **Nunca** ler, imprimir ou copiar valores do `.env` para código, testes, docs, logs ou commits. Variáveis novas vão no `.env.example`, **vazias**.
- **Não** fazer merge, rebase ou push. **Não** criar nada do M3 (`src/core`, `src/storage`, `src/services`, simulação, recomendação).
- Código do M1/M2: só os pontos da Fase 7, com mudanças mínimas.
- Testes unitários **sem rede**, usando o padrão do M2 (repositórios em memória). Variáveis do M4 necessárias aos testes são fixadas no `tests/conftest.py`, como já é feito com `PSEUDONYM_HMAC_KEY`. Credenciais de COS também são zeradas lá.
- Testes contra a cloud real ficam em `@pytest.mark.integration` (registrar o marker no `pytest.ini`), usam `test_fixture: true` e limpam tudo ao final.
- Python 3.12 (`.python-version`).
- **PROIBIDO `git commit`, `git push`, `git merge`, `git rebase`, `git stash` ou qualquer comando que altere o histórico ou a branch.** Ao fim de cada fase, apenas deixe as alterações no working tree e **pare**: o usuário revisa e commita ele mesmo.

---

## Fase 0 — Reconhecimento (pare ao final e reporte)

1. Ler `PLANO.md`, `docs/m1-consent-lifecycle.md`, `docs/m2-agent1.md`, `docs/infra-setup.md`, `src/api/models.py`, `src/privacy/pseudonymization.py`, `src/agents/agent1/` (em especial `repository.py`, `tools.py`, `sources.py`, `config.py`), `scripts/seed_test_consent.py`, `tests/conftest.py`, `tests/fakes.py` e `pytest.ini`.
2. Rodar `pytest -q` e anotar a base (quantos testes passam ou falham antes do M4).
3. Reportar no chat, sem criar arquivo:
   - confirmação de que o M3 **não** está na base;
   - como o M2 acessa o Cloudant e como os testes injetam o repositório;
   - onde nasce o `pseudonym_id` hoje (seed script / stub do M1);
   - onde `persist_snapshot` grava e qual é a estrutura de `agent.tool_calls`;
   - se `WATSONX_MODEL_ID` no `.env.example` é `ibm/granite-4-h-small`;
   - riscos encontrados.
4. **Parar e aguardar confirmação.**

---

## Fase 1 — Configuração

1. `requirements.txt`: adicionar `cryptography` e `ibm-cos-sdk` (se faltarem).
2. `.env.example`, nova seção (vazia):
   ```
   # ── M4 — Chaves, cofre de identidade, retenção e auditoria ───────────────────
   # Versão ativa da chave de pseudonimização. v1 = PSEUDONYM_HMAC_KEY (já existente).
   PSEUDONYM_KEY_ACTIVE_VERSION=v1
   # Chave mestra (KEK) do cofre de identidade, 32 bytes em hex.
   # Gerar: python -c "import secrets; print(secrets.token_hex(32))"
   VAULT_KEK_V1=
   VAULT_KEK_ACTIVE_VERSION=v1
   CLOUDANT_DB_IDENTITY_VAULT=identity_vault
   CLOUDANT_DB_AUDIT=audit_trail
   COS_AUDIT_PREFIX=audit/
   RETENTION_AFTER_REVOCATION_DAYS=90
   # Token dos endpoints /internal/* (gerar como as chaves acima)
   INTERNAL_API_TOKEN=
   ```
3. Validação na inicialização: com `APP_ENV=production`, a ausência de chave do M4 é erro fatal. Em dev, aviso no log (mesmo padrão do M2).
4. `tests/conftest.py`: fixar `VAULT_KEK_V1` e `INTERNAL_API_TOKEN` de teste e zerar `COS_ACCESS_KEY_ID` e `COS_SECRET_ACCESS_KEY`.

## Fase 2 — `src/privacy/key_provider.py` (+ ajuste em `pseudonymization.py`)

- `KeyProvider` (Protocol): `get(purpose: Literal["pseudonym", "vault_kek"], version: str | None = None) -> tuple[str, bytes]`.
- `EnvKeyProvider`: `pseudonym` v1 = `PSEUDONYM_HMAC_KEY`, vN = `PSEUDONYM_HMAC_KEY_VN`; `vault_kek` vN = `VAULT_KEK_VN`. Exige hex válido com no mínimo 32 bytes.
- `SecretsManagerKeyProvider`: esqueleto + docstring do fluxo de produção (Secrets Manager, HSM, Trusted Profile, role `SecretsReader`, cache curto, rotação).
- `pseudonymization.py`: obter a chave via `KeyProvider`, **mantendo** a chave efêmera em dev e **a mesma saída**. Adicionar `current_key_version()` e `pseudonymize_cpf_with_version(cpf) -> (pseudonym_id, key_version)`. Corrigir a docstring ("COS + envelope encryption").
- **Testes:** `tests/test_pseudonymization.py` passa **sem alteração**; teste de regressão com saída fixa conhecida; chave inválida ou curta → erro; múltiplas versões; produção sem chave → erro.

## Fase 3 — `src/privacy/identity_vault.py`

Repositório no padrão do M2: `VaultRepository` (Protocol) + `InMemoryVaultRepository` + `CloudantVaultRepository` (SDK `ibmcloudant`).

Documento em `identity_vault` (`_id = pseudonym_id`):
```json
{ "_id": "<pseudonym_id>", "type": "identity", "key_version": "v1", "kek_version": "v1",
  "wrapped_dek": "<b64>", "cpf_ciphertext": "<b64>", "nonce": "<b64>",
  "created_at": "ISO-8601Z", "shredded_at": null }
```
- `store(cpf) -> pseudonym_id`: pseudonimiza, gera uma DEK aleatória, cifra o CPF com AES-256-GCM (AAD = `pseudonym_id`) e envolve a DEK com a KEK. Idempotente: titular existente não é duplicado.
- `reveal(pseudonym_id, *, purpose, actor) -> str`: decifra e **sempre** registra o evento `vault.reveal` (Fase 5). `purpose` aceita só valores de uma lista fechada (`SERASA_QUERY`, `LEGAL_REQUEST`).
- `shred(pseudonym_id)`: remove `wrapped_dek`, `cpf_ciphertext` e `nonce`, e preenche `shredded_at` (o tombstone fica como evidência).
- `resolve_for_serasa(pseudonym_id)`: chama `reveal(purpose="SERASA_QUERY", actor="agent1")`. **Não** alterar o `HttpSerasaClient`; documentar o ponto de ligação em `docs/m4-chaves-e-iam.md`.
- **Testes:** ida e volta; `store` idempotente; `reveal` depois de `shred` → erro; AAD trocado → falha de integridade; `reveal` sempre gera auditoria; o CPF nunca aparece no documento persistido.

## Fase 4 — Guarda de PII

- `src/privacy/pii_guard.py`: `assert_no_pii(doc)` faz uma varredura recursiva e rejeita chaves (`cpf`, `nome`, `name`, `email`, `telefone`, `phone`, `endereco`, `address`, `contractId`, `contract_id`, `contract_number`) e valores com padrão de CPF (11 dígitos, com ou sem máscara). Deve **aceitar** um snapshot válido do M2 (inclusive `contract_ref`, `debt_id` e `pseudonym_id`).
- **Testes:** um snapshot real gerado pelos fakes do M2 passa; casos com CPF ou chave proibida, inclusive aninhados, falham.

## Fase 5 — Auditoria: `src/privacy/audit.py`

- `AuditRepository` (Protocol) + memória + Cloudant (`audit_trail`), no padrão do M2.
- `record_event(event_type, *, pseudonym_id, actor, **fields) -> trace_id` (UUID4). Registro: `trace_id`, `recorded_at`, `event_type`, `pseudonym_id`, `actor` + campos; passa pela guarda de PII.
- Cópia no COS em `COS_AUDIT_PREFIX/AAAA/MM/DD/<trace_id>.json` (bucket `COS_BUCKET_NAME`, endpoint `s3.us-south.cloud-object-storage.appdomain.cloud`, credenciais HMAC via `ibm-cos-sdk`). Sem credenciais de COS (testes, dev), só o Cloudant/memória.
- **Falhas de auditoria não derrubam o fluxo principal:** log de erro estruturado (sem PII).
- `GovernanceSink` (Protocol) com `NoopGovernanceSink` ativo e `WatsonxGovernanceSink` como esqueleto + docstring (AI Factsheets, `trace_id` como chave de correlação).
- Eventos da Parte A: `consent.expired`, `consent.erasure_requested`, `erasure.completed`, `vault.reveal`, `retention.run`, `agent1.snapshot_persisted`.
- **Testes:** `trace_id` retornado; evento com CPF é rejeitado; falha do COS não propaga; cópia no COS chamada com a chave de objeto correta (client mockado).

## Fase 6 — `src/privacy/retention.py` (substitui o TTL do MongoDB)

| Objeto | Regra |
|---|---|
| Consentimento `AWAITING_AUTHORISATION` | `awaiting_expires_at` < agora → apagar |
| Consentimento `AUTHORISED` | `expires_at` < agora → `REVOKED`, `revocation_reason=EXPIRED`, `revoked_at=agora` + evento `consent.expired` |
| Consentimento `REVOKED` (motivo diferente de `LGPD_ERASURE`) | mantido até `revoked_at + RETENTION_AFTER_REVOCATION_DAYS`, depois apagado |
| Snapshot (`financial_snapshots`) | apagar quando `expires_at` < agora. Se o consentimento foi revogado antes, usar `min(expires_at, revoked_at + RETENTION_AFTER_REVOCATION_DAYS)` |
| `identity_vault` | sem consentimento nem snapshot retidos → `shred` |
| `audit_trail` | **nunca** expurgar |

- `run_retention(now=None, dry_run=False) -> RetentionReport` (contagens por regra), idempotente, com relógio e repositórios injetáveis.
- Índices Mango idempotentes (`ensure_indexes()`), além dos que o M2 já criou: `consent_registry` → `["status", "awaiting_expires_at"]`, `["expires_at"]`, `["pseudonym_id", "status"]`; `audit_trail` → `["pseudonym_id", "recorded_at"]`, `["event_type", "recorded_at"]`. `ensure_databases()` cria `identity_vault` e `audit_trail` se faltarem.
- Execução: `python -m src.privacy.retention [--dry-run]` **e** `POST /internal/retention/run` (Fase 7).
- Evento `retention.run` com as contagens.
- **Testes:** uma linha da tabela por teste; `dry_run` não apaga nada; a segunda execução não faz nada.

## Fase 7 — Pontos de integração com M1 e M2 (mínimos)

| Onde | Mudança |
|---|---|
| `scripts/seed_test_consent.py` | Gerar o `pseudonym_id` via `identity_vault.store(SANDBOX_CPF)`; no `delete`, também fazer o `shred` do titular de teste |
| `src/agents/agent1/tools.py` → `persist_snapshot` | Antes de `save_snapshot`: `assert_no_pii(snapshot)` e incluir `key_version`. Depois de salvar: `record_event("agent1.snapshot_persisted", pseudonym_id=…, actor="agent1", snapshot_id=…, model_id=…, tool_calls=…)`. Falha da auditoria não falha a tool |
| `src/api/routers/consent.py` (M1, stub) | **Não implementar.** Só atualizar o `TODO (M1)` para apontar para `identity_vault.store(cpf)` e `record_event("consent.created")` |
| Novo `src/api/routers/internal.py` | Tag `Internal`, protegido por header `X-Internal-Token` comparado com `hmac.compare_digest` a `INTERNAL_API_TOKEN` (ausente → 503; inválido → 401) |
| `src/main.py` | Registrar o router `internal` |

Endpoints internos:
- `POST /internal/erase/{pseudonym_id}` (LGPD Art. 18), implementando o passo 3c do M1:
  1. evento `consent.erasure_requested`;
  2. consentimentos do titular → `REVOKED`, `revocation_reason=LGPD_ERASURE` (a revogação de token OAuth é do M1);
  3. apagar os snapshots do titular (índice `by-pseudonym`);
  4. `identity_vault.shred`;
  5. evento `erasure.completed` → `200 {consents_revoked, snapshots_deleted, vault_shredded, trace_id}`.
  Idempotente: a segunda chamada devolve zeros. *(As decisões do M3 entram na Parte B.)*
- `POST /internal/retention/run?dry_run=true|false` → `RetentionReport`.
- Documentar os dois endpoints no `openapi/openapi.yaml` (tag `Internal`, segurança por header).
- **Testes:** os testes do M2 (`test_agent1`, `test_debts_api` e os demais) continuam verdes; snapshot com PII injetada → a tool falha com erro claro; auditoria do snapshot gravada; erase e retention via `TestClient`, com e sem token.

## Fase 8 — Testes ponta a ponta

1. `tests/m4/` com os testes das fases (sem rede); `pytest -q` mantém verde **tudo o que já passava**.
2. `tests/m4/test_integration.py` (`@pytest.mark.integration`), contra Cloudant e COS reais:
   - `ensure_databases()` / `ensure_indexes()`;
   - seed via cofre → `GET /debts` (Granite real; OF e Serasa em sandbox) → conferir snapshot sem PII + evento `agent1.snapshot_persisted` em `audit_trail` e no COS;
   - `POST /internal/erase` → snapshot apagado, cofre destruído, `reveal` falha;
   - `run_retention(dry_run=True)` com documentos vencidos de teste.
   Tudo com `test_fixture: true`, limpo ao final.
3. Comandos:
   ```bash
   pytest -q -m "not integration"
   pytest -q -m integration
   uvicorn src.main:app --reload   # conferir /docs
   ```

## Fase 9 — Documentação

| Arquivo | Conteúdo |
|---|---|
| `docs/m4-mapeamento-dados.md` | Todo campo pessoal ou financeiro tratado pelo M1/M2 (CPF, saldos, contratos, score, instituições): origem, finalidade, onde fica, tratamento (pseudonimizado, cifrado, faixa) |
| `docs/m4-ropa.md` | ROPA (LGPD): controlador, finalidades, base legal (Art. 7º, I), titulares, categorias, compartilhamentos (Open Finance, Serasa, IBM Cloud), retenção, segurança (Art. 46), direitos (Art. 18) |
| `docs/m4-retencao-e-delecao.md` | Tabela da Fase 6, motivo do job (Cloudant sem TTL), fluxo do Art. 18, crypto-shredding, prazo de 15 dias |
| `docs/m4-chaves-e-iam.md` | Hackathon (env + `KeyProvider`) × produção (Secrets Manager + HSM, Trusted Profile), rotação com `key_version`, re-identificação para o Serasa, quem acessa qual chave e com qual role |
| `docs/m4-auditoria-governanca.md` | Estrutura dos eventos, destinos (Cloudant + COS), plano para watsonx.governance (AI Factsheets), alertas de acesso anômalo no Activity Tracker (leituras em massa no `identity_vault`, `reveal` fora do padrão, falhas de auth) |

Atualizar também:
- `PLANO.md` → Sub-Tarefa 4: status "Parte A concluída", checklist e entregáveis (formato da Sub-Tarefa 1); na tabela de decisões, trocar "COS + envelope encryption" por "Env + KeyProvider; envelope encryption com DEK por titular" e adicionar "Retenção no Cloudant via job de expurgo".
- `docs/m2-agent1.md` §8 e §12 → marcar as pendências do M4 resolvidas na Parte A e corrigir o texto da chave.
- `docs/infra-setup.md` → `curl` do Cloudant com token IAM (não a API key como Bearer); novos bancos; modelo `granite-4-h-small`.

---

## Definição de Pronto (Parte A)

- [ ] Fase 0 reportada e aprovada
- [ ] `pytest -q -m "not integration"` verde, sem regressão nos testes do M2
- [ ] `pytest -q -m integration` verde na cloud real, sem resíduo de dados de teste
- [ ] `pseudonymize_cpf` e `pseudonymize_contract` com saída idêntica à anterior (teste de regressão)
- [ ] Cofre de identidade com `store`, `reveal` auditado e `shred` funcionando
- [ ] Snapshot do Agente 1 validado pela guarda de PII e auditado em `audit_trail` + COS
- [ ] `/internal/erase/{pseudonym_id}` e `/internal/retention/run` funcionais, protegidos e idempotentes
- [ ] Nenhum CPF em claro em snapshots, `audit_trail`, logs ou COS (verificado por teste)
- [ ] `openapi.yaml`, `PLANO.md`, `m2-agent1.md`, `infra-setup.md` e os 5 docs `m4-*` atualizados
- [ ] Nenhum segredo no diff (`git diff` revisado; `.env` intocado no git)
- [ ] Nenhum commit feito pelo executor (`git log` inalterado; commits só pelo usuário)

---

## Parte B (NÃO executar agora — virá em outro prompt, após o M3 entrar)

Registrado aqui só para o executor saber o que **não** fazer:
- Fazer `services/governance.record_trace` do M3 delegar para `src/privacy/audit.py` (trace em `audit_trail` + COS, guarda de PII no prompt e na saída, hashes)
- Separar simulações e recomendações em `decisions` (`CLOUDANT_DB_DECISIONS`) e incluí-las na retenção e no erase
- Adaptar o M4 à camada de storage que a integração M2 × M3 adotar
- Teste ponta a ponta `GET /debts` → `POST /simulation` → `POST /recommendation` com `trace_id` auditado
