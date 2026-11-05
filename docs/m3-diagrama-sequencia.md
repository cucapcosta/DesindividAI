# M3 — Diagramas de Sequência

Diagramas baseados no código real de `src/api/routers/`, `src/services/`, `src/simulation/client.py` e `src/agents/recommendation_agent.py`.

---

## 1. `POST /simulation` — Simulação de cenários de quitação

```mermaid
sequenceDiagram
    autonumber
    actor Canal as Canal (app/parceiro)
    participant APIC as IBM API Connect
    participant API as FastAPI<br/>(simulation router)
    participant SVC as simulation_service
    participant DB as Cloudant
    participant ENG as Motor de Simulação

    Canal->>APIC: POST /simulation<br/>{ snapshot_id, monthly_budget }
    APIC->>API: encaminha requisição

    API->>SVC: create_simulation(snapshot_id, monthly_budget)

    %% ── Carrega snapshot ──────────────────────────────────────────────────────
    SVC->>DB: GET financial_snapshots/{snapshot_id}
    alt snapshot inexistente
        DB-->>SVC: null
        SVC-->>API: ServiceError 404 SNAPSHOT_NOT_FOUND
        API-->>Canal: 404 { code: SNAPSHOT_NOT_FOUND }
    end
    DB-->>SVC: snapshot { pseudonym_id, debts, … }

    %% ── Consentimento (opcional) ──────────────────────────────────────────────
    alt REQUIRE_CONSENT=true
        SVC->>DB: find consent_registry<br/>{ pseudonym_id, status: "AUTHORISED" }
        alt sem consentimento ativo
            DB-->>SVC: []
            SVC-->>API: ServiceError 403 CONSENT_REQUIRED
            API-->>Canal: 403 { code: CONSENT_REQUIRED }
        end
        DB-->>SVC: [ registro AUTHORISED ]
    end

    %% ── Valida dívidas ────────────────────────────────────────────────────────
    alt debts ausentes ou lista vazia
        SVC-->>API: ServiceError 422 SNAPSHOT_INVALID
        API-->>Canal: 422 { code: SNAPSHOT_INVALID }
    end

    %% ── Motor de Simulação ────────────────────────────────────────────────────
    alt WATSONX_SIMULATION_URL configurado
        SVC->>ENG: POST {url}/predict<br/>{ debts, monthly_budget, bb_cet_annual }<br/>Authorization: Bearer <IAM token>
        alt Motor indisponível / HTTP error
            ENG-->>SVC: HTTPError
            SVC-->>API: ServiceError 503 SERVICE_UNAVAILABLE
            API-->>Canal: 503 { code: SERVICE_UNAVAILABLE }
        end
        ENG-->>SVC: { scenarios: [ agressivo, consolidação, mínimo ] }
    else sem WATSONX_SIMULATION_URL (execução local)
        SVC->>ENG: simulate(debts, monthly_budget, bb_cet) [thread pool]
        alt entrada inválida
            ENG-->>SVC: ValueError
            SVC-->>API: ServiceError 400 INVALID_INPUT
            API-->>Canal: 400 { code: INVALID_INPUT }
        end
        ENG-->>SVC: [ agressivo, consolidação, mínimo ]
    end

    %% ── Persiste resultado ────────────────────────────────────────────────────
    SVC->>DB: PUT decision_traces/simulation:{simulation_id}<br/>{ type, pseudonym_id, scenarios, … }
    DB-->>SVC: ok

    SVC-->>API: SimulationResponse
    API-->>APIC: 200 { simulation_id, snapshot_id, scenarios, generated_at }
    APIC-->>Canal: 200 { simulation_id, snapshot_id, scenarios, generated_at }
```

---

## 2. `POST /recommendation` — Recomendação em linguagem simples

```mermaid
sequenceDiagram
    autonumber
    actor Canal as Canal (app/parceiro)
    participant APIC as IBM API Connect
    participant API as FastAPI<br/>(recommendation router)
    participant SVC as recommendation_service
    participant DB as Cloudant
    participant GOV as governance<br/>(record_trace)
    participant LLM as Granite<br/>(watsonx.ai)

    Canal->>APIC: POST /recommendation<br/>{ simulation_id, language_level }
    APIC->>API: encaminha requisição

    %% ── Instancia LLM (Depends) ───────────────────────────────────────────────
    API->>LLM: build_llm() — conecta ChatWatsonx
    alt watsonx.ai não configurado ou credencial inválida
        LLM-->>API: ServiceError 503 SERVICE_UNAVAILABLE
        API-->>Canal: 503 { code: SERVICE_UNAVAILABLE }
    end

    API->>SVC: create_recommendation(simulation_id, language_level)

    %% ── Carrega simulação ─────────────────────────────────────────────────────
    SVC->>DB: GET decision_traces/simulation:{simulation_id}
    alt simulação inexistente
        DB-->>SVC: null
        SVC-->>API: ServiceError 404 SIMULATION_NOT_FOUND
        API-->>Canal: 404 { code: SIMULATION_NOT_FOUND }
    end
    DB-->>SVC: { pseudonym_id, snapshot_id, scenarios, … }

    %% ── Consentimento (opcional) ──────────────────────────────────────────────
    alt REQUIRE_CONSENT=true
        SVC->>DB: find consent_registry<br/>{ pseudonym_id, status: "AUTHORISED" }
        alt sem consentimento ativo
            DB-->>SVC: []
            SVC-->>API: ServiceError 403 CONSENT_REQUIRED
            API-->>Canal: 403 { code: CONSENT_REQUIRED }
        end
        DB-->>SVC: [ registro AUTHORISED ]
    end

    %% ── Carrega snapshot (primeiros passos) ───────────────────────────────────
    SVC->>DB: GET financial_snapshots/{snapshot_id}
    note over SVC,DB: Falha silenciosa — primeiros passos ficam vazios<br/>se o snapshot não for encontrado
    DB-->>SVC: snapshot { debts } (ou null → debts=[])

    %% ── Cadeia LCEL ───────────────────────────────────────────────────────────
    note over SVC: build_chain(llm).ainvoke(...)
    SVC->>SVC: select_recommended(scenarios)<br/>→ min(total_interest_paid), desempate por total_months
    SVC->>SVC: with_steps(recommended)<br/>→ primeiros passos do mês 1
    SVC->>SVC: _facts(...) → JSON pseudonimizado
    SVC->>SVC: PROMPT (system + human) formatado

    SVC->>LLM: ChatWatsonx.invoke(prompt)<br/>modelo: WATSONX_MODEL_ID (padrão: ibm/granite-4-h-small)<br/>max_tokens: 600, temperature: 0.2
    alt LLM indisponível / erro de inferência
        LLM-->>SVC: Exception
        SVC-->>API: ServiceError 503 SERVICE_UNAVAILABLE
        API-->>Canal: 503 { code: SERVICE_UNAVAILABLE }
    end
    LLM-->>SVC: explanation (texto em português)

    %% ── Grava trace de governança ─────────────────────────────────────────────
    SVC->>GOV: record_trace(recommendation_id, simulation_id,<br/>snapshot_id, pseudonym_id, prompt, output, …)
    GOV->>DB: PUT decision_traces/trace-{uuid}<br/>{ type, trace_id, model_id, selection_rule,<br/>recommended_scenario, prompt, output, recorded_at }
    DB-->>GOV: ok
    GOV-->>SVC: trace_id

    SVC-->>API: RecommendationResponse
    API-->>APIC: 200 { recommendation_id, recommended_scenario,<br/>explanation, scenarios, generated_at, trace_id }
    APIC-->>Canal: 200 { recommendation_id, recommended_scenario,<br/>explanation, scenarios, generated_at, trace_id }
```

---

## Onde cada coisa roda

| Componente | Ambiente de execução |
|---|---|
| **FastAPI** (routers, services, models) | Contêiner local / IBM Code Engine |
| **Motor de Simulação** (`simulate`) — modo padrão | Thread pool local no mesmo processo (`asyncio.to_thread`) |
| **Motor de Simulação** — modo produção | AI Service externo em **watsonx.ai** (`WATSONX_SIMULATION_URL/predict`) |
| **Agente 2 / LLM** (`ChatWatsonx` + Granite) | **watsonx.ai** — sempre remoto (requer `WATSONX_URL`, `IBM_CLOUD_API_KEY` e `WATSONX_PROJECT_ID` ou `WATSONX_SPACE_ID`) |
| **Snapshots financeiros** (`financial_snapshots`) | **IBM Cloudant** — banco de snapshots do Agente 1 |
| **Simulações, recomendações e traces** (`decision_traces`) | **IBM Cloudant** — banco separado de acesso restrito |
| **Consentimentos** (`consent_registry`) | **IBM Cloudant** — lido opcionalmente quando `REQUIRE_CONSENT=true` |
| **IBM API Connect** | Gateway IBM Cloud — autenticação, rate-limit e roteamento para todos os endpoints |
