# IR Credit Request Investigation & Resolution Agent

Agentic AI layer on top of IR, a mortgage credit-reporting service that pulls borrower credit reports from bureaus
(Equifax, TransUnion) on behalf of lenders. When a request fails, stalls, or errors, an operations user asks
the agent *"Why did credit request 12345 fail?"* — the agent investigates across the IR backend and internal
documentation, explains what happened, and proposes the approved next step. Sensitive actions (retry) require
human approval before the backend executes them.

**The bureau integrations are not implemented.** `mock_backend/` returns seeded synthetic responses. All error codes,
procedures, lenders, and borrower references are invented.

## Architecture

```
Ops user ──▶ POST /api/v1/investigations (202) ──▶ background: ADK investigator agent
                                                       │  decides which tools to call:
                                                       ├─ get_credit_request   ─┐
                                                       ├─ get_bureau_status    ─┼─▶ IR backend service layer (HTTP)
                                                       ├─ get_error_details    ─┘      (mock locally)
                                                       ├─ search_knowledge_base ──▶ pgvector RAG (internal docs)
                                                       └─ propose_action ────────▶ PendingAction (NOT executed)
                                                       ▼
                                   Investigation.answer + tool_trace + citations
                                                       │
       human: POST /api/v1/actions/{id}/decision ──▶ APPROVE ──▶ backend.retry_credit_request (X-Approved-By)
```

Three rules the code enforces:
1. **Agent → service layer → data, never agent → database.** Tools call REST endpoints; borrower data is never stored in the agent service.
2. **Agents propose, humans approve, backend executes.** There is no execute tool; `propose_action` only records intent.
3. **HITL is for actions, not every answer.** Read-only investigations complete without a human; only a proposed retry pauses for approval.

## Requirement → implementation map

| Requirement | Where |
|---|---|
| Python backend + GenAI via REST | `app/main.py`, `app/api/routes.py` |
| Agentic workflows with Google ADK (agents, tools, instructions, orchestration) | `app/agent/investigator.py` (`LlmAgent`, dynamic tool orchestration), `app/agent/tools.py`, `app/prompts/investigator.md` |
| Vertex AI: contextual processing, response generation, prompt management | `app/llm/vertex.py` (google-genai, Vertex backend), `app/llm/prompts.py` (versioned prompt files) |
| RAG: embeddings, semantic search, enterprise retrieval | `app/rag/` — heading-aware chunker, `text-embedding-005`, pgvector cosine search |
| Async services, long-running workflows | 202 + `BackgroundTasks` + polling in `app/workflows/`; async SQLAlchemy; `httpx.AsyncClient`; `asyncio.to_thread` around SDK calls |
| PostgreSQL / Cloud SQL, complex queries, persistence | `app/db/models.py` (investigations, pending actions, knowledge chunks — JSONB traces for audit), Cloud SQL Auth Proxy sidecar |
| HITL + LLM evals | `PendingAction` gate + `POST /actions/{id}/decision`; `evals/run_evals.py` (tool coverage, error-code accuracy, action gating, grounding, LLM-as-judge) as a CI gate |
| Docker / K8s / GKE / GCP / CI-CD / observability | `Dockerfile`s, `docker-compose.yml`, `k8s/` (HPA, probes, Workload Identity), `cloudbuild.yaml`, `app/observability.py` (Cloud Logging + Cloud Trace via OpenTelemetry) |

## Run locally

```bash
cp .env.example .env                       # set GOOGLE_CLOUD_PROJECT
gcloud auth application-default login
docker compose up -d db ir-backend
pip install -r requirements.txt
python -m app.rag.ingest data/knowledge/ir_troubleshooting_guide.md
uvicorn app.main:app --reload --port 8080

# investigate
curl -X POST localhost:8080/api/v1/investigations -H 'content-type: application/json' \
  -d '{"question":"Why did credit request 12345 fail?","requested_by":"ops.jeev"}'
curl localhost:8080/api/v1/investigations/<id>            # poll: RUNNING -> AWAITING_APPROVAL

# human approves the proposed TransUnion retry
curl localhost:8080/api/v1/actions/pending
curl -X POST localhost:8080/api/v1/actions/<action_id>/decision -H 'content-type: application/json' \
  -d '{"decided_by":"ops.jeev","decision":"APPROVE","notes":"Lender verified borrower address"}'
```

Seeded scenarios in the mock backend: `12345` partial failure (TU identity mismatch → retry TU only), `12346` double timeout
at retry limit, `12347` frozen file (must NOT retry), `12348` in progress, `12349` success, `99999` not found.

Swagger: http://localhost:8080/docs (agent) · http://localhost:9000/docs (mock backend)

## Evals
```bash
python -m evals.run_evals     # exit 1 on threshold failure; action gating must be 100%
```
Add a golden case every time an ops user disagrees with the agent — that becomes your regression suite.

## Deploy to GKE
Artifact Registry repo `ir`, GKE cluster `ir-cluster`, Cloud SQL Postgres 16 `ir-agent-db` with `CREATE EXTENSION vector`,
Workload Identity binding for `ir-credit-agent-sa`, Secret Manager `ir-agent-database-url`. Then `gcloud builds submit`.

## Next steps
- `DatabaseSessionService` for multi-turn follow-ups ("what about the Equifax side?") across replicas.
- Cloud Tasks / Pub/Sub instead of `BackgroundTasks` for durable queuing.
- Alembic migrations; per-tool spans in Cloud Trace; token/latency metrics per investigation.
