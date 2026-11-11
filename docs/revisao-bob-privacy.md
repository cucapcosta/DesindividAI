# Revisão de Código — Módulo `src/privacy/`

> **Escopo:** `audit.py`, `retention.py`, `retention_store.py`, `identity_vault.py`, `pii_guard.py`
> **Foco:** conformidade LGPD, bugs e divergências com `docs/m4-*.md`
> **Data:** revisão pontual; base = código no branch `chore/bob-ajustes`

## Resumo

O módulo implementa com solidez os controles centrais exigidos pela LGPD (pseudonimização, crypto-shredding, trilha de auditoria, guarda de PII e job de retenção). Foram encontrados três bugs concretos de baixo risco imediato, duas divergências entre o código e a documentação, e cinco pontos de conformidade que merecem atenção antes de uma entrada em produção real. Nenhum achado expõe CPF ou dado pessoal em texto claro.

---

## Tabela de Achados

| # | Arquivo : linha | Severidade | Problema | Sugestão | Status |
|---|---|---|---|---|---|
| 1 | `audit.py:172` | Baixa | **Inicialização morta de `_store`.** `self._store: list[dict] = {}` atribui um `dict` vazio onde o tipo declarado é `list[dict]`. A linha seguinte (`self._store = []`) corrige isso em tempo de execução, mas o comentário `# type: ignore[assignment]` revela que o mypy já sinalizou. Em produção o `InMemoryAuditRepository` não é usado, então o risco é zero em runtime; em testes o `dict` nunca chega a ser iterado. | Remover a linha `self._store: list[dict] = {}` e manter apenas `self._store: list[dict] = []`. | **Corrigido** |
| 2 | `retention.py:186–189` | Média | **Double-scan em `iter_consents({})` para construir `consent_by_id`.** O código itera `store.iter_consents({})` duas vezes no início de `_apply_rules()`: primeiro na linha 175 (filtro `REVOKED`) e depois em 187 (todos os consentimentos). No `CloudantRetentionStore` cada chamada emite uma série de requisições HTTP paginadas. Se a coleção tiver N documentos e _PAGE_SIZE = 200, isso são ⌈N/200⌉ × 2 requisições onde bastaria uma só passagem. Sem risco de dado errado, mas com risco de saturar o limite de requisições do Cloudant Lite (429) em bases maiores, especialmente porque o retry exponencial apenas adia o problema. | Fazer o índice `consent_by_id` e o índice `revoked_consents` na mesma iteração única de `iter_consents({})`, eliminando a segunda passagem. | Aberto |
| 3 | `retention.py:395–422` | Alta (LGPD) | **Mutação do dict `doc` antes do `update_consent`, sem cópia defensiva.** Na regra c, o código faz `doc["status"] = "REVOKED"` etc. diretamente no dicionário retornado por `store.iter_consents()`. No `InMemoryRetentionStore`, o `iter_consents` já retorna `dict(doc)` (cópia rasa), então a mutação não contamina o store em testes. No `CloudantRetentionStore`, porém, o Cloudant SDK pode devolver objetos compartilhados ou o dicionário pode ser referenciado por outra parte do código (ex.: o `consent_by_id` construído na linha 189 aponta para o **mesmo** objeto). Se o `update_consent` falhar e o fluxo fizer `continue`, o objeto em `consent_by_id` já estará marcado como `REVOKED` mesmo que o banco não tenha sido atualizado — o que pode fazer a regra a ou a2 aplicar o cálculo de `min(expires_at, revoked_at + 90d)` indevidamente nessa execução. Isso é um bug silencioso de state corruption que viola o princípio de retenção mínima da LGPD Art. 46 (medidas técnicas adequadas). | Criar uma cópia antes de mutar: `doc = dict(doc)` ou `doc = {**doc, "status": "REVOKED", "revocation_reason": "EXPIRED", "revoked_at": now_iso}` antes de chamar `store.update_consent(doc)`. | Aberto |
| 4 | `retention_store.py:185–203` | Baixa | **`_matches` não implementa `$and` nem short-circuit entre operadores.** Se um valor for `{"$in": [...], "$lt": "..."}` ao mesmo tempo, ambos os operadores são avaliados, mas sem `return False` imediato após o `$in` falhar. Na prática os seletores usados em `retention.py` nunca combinam `$in` e `$lt` no mesmo campo, então não gera resultado incorreto hoje. | Adicionar `return False` (ou `continue`) após o bloco `$in` para short-circuit, alinhando o comportamento à semântica Mango. | Aberto |
| 5 | `identity_vault.py:396–398` | Média (LGPD) | **Mensagem de exceção `VaultError` inclui `pseudonym_id` em texto claro.** `raise VaultError(f"Falha ao decifrar CPF do titular {pseudonym_id!r}: {exc}")`. O `pseudonym_id` é um pseudônimo (HMAC-SHA256), não o CPF, e a doc (`m4-mapeamento-dados.md §2`) o classifica como dado pessoal pseudonimizado. Se a exceção for capturada e logada sem tratamento (ex.: em um middleware de erro genérico), o pseudônimo entra nos logs. A linha 366 tem o mesmo padrão: `f"Titular {pseudonym_id!r} não encontrado..."`. | Substituir pelo `trace_id` ou omitir o `pseudonym_id` da mensagem: `raise VaultError("Falha ao decifrar CPF: chave ou ciphertext corrompido.")`. | **Corrigido** |
| 6 | `pii_guard.py:34–53` | Média (LGPD) | **`_FORBIDDEN_KEYS` não inclui `data_nascimento`, `birth_date`, `cns` (Cartão Nacional de Saúde) nem `passport`.** A LGPD Art. 5º, II exige especial atenção a dados sensíveis; mesmo não sendo dados sensíveis stricto sensu, `data_nascimento` e `birth_date` são identificadores diretos. O `m4-ripd.md §4 R3` aponta "vazamento de PII em logs" como risco médio. Se um campo `birth_date` entrar num `**fields` de `record_event`, ele passará pela guarda sem bloqueio. | Adicionar `"data_nascimento"`, `"birth_date"`, `"date_of_birth"` (e equivalentes) à `_FORBIDDEN_KEYS`. | **Corrigido** |
| 7 | `audit.py:470–481` | Média | **Loop `while True` sem limite de iterações no sanitizador do `audit.pii_blocked`.** Se `assert_no_pii(blocked_record)` continuar levantando `PIIViolation` em caminhos inesperados (ex.: `violation_path` que o código `pop` não consegue remover porque o caminho usa notação de lista como `"fields[0]"`), o loop nunca termina. Isso bloquearia a thread do servidor indefinidamente. | Adicionar um contador de segurança (ex.: `max_iterations = 20`) com `break` e `logger.error` ao esgotar, antes de chamar `self._dispatch(blocked_record)`. | **Corrigido** |
| 8 | `retention.py:187` | Baixa (divergência doc) | **Divergência com `docs/m4-retencao-e-delecao.md §2`.** A doc descreve a regra a como usando `consent_id` do snapshot para buscar o consentimento. O código (linha 187) constrói o índice `consent_by_id` com `doc.get("consent_id") or doc.get("_id", "")`, aceitando o `_id` do consentimento como fallback. Se `consent_id` e `_id` forem diferentes (o que é normal — o `_id` Cloudant é geralmente o `consent_id`, mas pode ser outro UUID em migrações), a chave usada pode não coincidir com o `consent_id` do snapshot, e o fallback para `revoked_consents` (por `pseudonym_id`) seria invocado erroneamente. A doc não menciona esse fallback; ele não está documentado como comportamento intencional. | Documentar o fallback no `m4-retencao-e-delecao.md` ou, preferível, remover o fallback e logar um warning quando `consent_id` estiver ausente. | Aberto |
| 9 | `audit.py:559–563` | Alta (LGPD) | **`AuditWriteError` é levantada com `event_type` e `trace_id` na mensagem.** `raise AuditWriteError(f"Nenhum destino de auditoria disponível (event_type={event_type} trace_id={trace_id})")`. O `trace_id` é o UUID4 do evento — isolado não é PII, mas o `event_type` pode ser `vault.reveal` que, combinado com contexto de stack trace em ferramentas de APM, pode ser correlacionado. Mais importante: a doc `m4-auditoria-governanca.md §1` afirma que "a mensagem não contém dados pessoais". Isso está correto hoje, mas deixa a mensagem frágil para futuras evoluções. Baixo risco hoje. | Manter a mensagem minimalista: `"Nenhum destino de auditoria disponível."` e registrar os detalhes apenas no `logger.error` já emitido antes. | Aberto |
| 10 | `retention_store.py:337–353` | Baixa (divergência doc) | **`ensure_databases` cria apenas `identity_vault`, `audit_trail` e `decision_traces`, mas não `consent_registry` nem `financial_snapshots`.** O docstring diz "Cria identity_vault, audit_trail e decision_traces", o que está correto — mas `docs/m4-retencao-e-delecao.md §6` lista os índices de `consent_registry` e `financial_snapshots` como criados por `ensure_indexes()`, sem mencionar que esses bancos precisam existir previamente. Em um ambiente zerado, `ensure_indexes` tentará criar índices em bancos inexistentes e falhará com 404. | Documentar explicitamente em `m4-retencao-e-delecao.md` que `consent_registry` e `financial_snapshots` são criados pelo M1/M2 e não pelo M4. Ou estender `ensure_databases` para criá-los também. | Aberto |

---

## Pontos Fortes

**1. Guarda de PII (`pii_guard.py`) bem calibrada.**
A validação combina chaves proibidas (comparação case-insensitive), detecção de CPF mascarado embedded (regex com lookahead/lookbehind anti-falso-positivo) e validação de dígitos verificadores. A exclusão de identificadores técnicos hexadecimais e UUIDs (`_TECHNICAL_ID`) evita falsos positivos no `pseudonym_id` — detalhe difícil de acertar que foi acertado.

**2. Trilha de auditoria fail-closed e com lista fechada de eventos.**
`_ALLOWED_EVENT_TYPES` (linha 136) + checagem de campos reservados (`_RESERVED_FIELDS`, linha 153) antes de qualquer gravação impedem que chamadores descuidados adicionem campos inesperados ou tipos de evento não previstos. A lógica de `audit.pii_blocked` como substituto do evento original (não como supressão silenciosa) é exatamente o que o Art. 37 da LGPD exige em termos de rastreabilidade.

**3. Crypto-shredding correto e idempotente (`identity_vault.py`).**
O `shred()` remove os campos criptográficos (`wrapped_dek`, `dek_nonce`, `cpf_ciphertext`, `nonce`) do documento corrente com um `put`, preservando o tombstone. A idempotência (`return False` se já destruído) torna o fluxo seguro em retentativas. A documentação sobre a limitação em cópias antigas (revisões do Cloudant, backups) está honestamente descrita tanto no código (docstring linha 26–28) quanto na doc (`m4-retencao-e-delecao.md §3`).

**4. Job de retenção robusto com `try/finally` garantindo `retention.run`.**
O bloco `try/finally` em `retention.py` (linha 562–592) garante que o evento `retention.run` é emitido mesmo em caso de exceção inesperada, com `aborted=True`. Isso satisfaz o requisito de rastreabilidade do Art. 37 LGPD e facilita auditoria operacional.

**5. `reveal()` com defesa em profundidade.**
A sequência `purpose_actors` → `consent_checker` → `_repo.get` → descriptografia implementa fail-closed em todas as camadas. O `consent_checker` levanta acesso negado se a consulta ao Cloudant falhar (linha 341), o que é o comportamento correto para dados pessoais. Cada ponto de falha gera um evento `vault.reveal ok=false` auditado.

**6. Retry com backoff exponencial em operações Cloudant.**
`_with_429_retry` (`retention_store.py` linha 34–61) com até 5 tentativas e cap de 8 s protege operações críticas de apagamento contra o rate limit do Cloudant Lite, sem mascarar outros erros (qualquer código != 429 propaga imediatamente).

---

## Verificação humana dos achados de severidade alta

| # | Veredito | Motivo |
|---|---|---|
| 3 | Falso positivo | A regra c itera uma chamada nova de `store.iter_consents({"status": "AUTHORISED"})` (`retention.py:379`), que devolve dicts novos (`dict(doc)` no store em memória, JSON novo no Cloudant). Não compartilha objeto com `consent_by_id`, e a regra a já terminou quando a regra c muta o documento. |
| 9 | Rebaixado para baixa | A mensagem só traz `event_type` e `trace_id` (UUID4), nenhum dado pessoal, como o próprio achado reconhece. |
