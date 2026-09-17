# Interview Q&A — IR Credit Request Investigation & Resolution Agent

Every answer quotes the real code. Where the interviewer would expect something that isn't in
the repo, the answer says **"not built — planned"** rather than inventing it.

---

## 1. Tool design and ADK schema generation

**Q: How do you define tools for the ADK agent — do you hand-write JSON schemas?**

No. ADK generates the function-calling schema from the Python function signature, type hints,
and docstring. `app/agent/tools.py` states the rule up front:

```python
"""
ADK tools. Docstrings + type hints become the schema the model sees.
...
"""
```

Each tool is a plain async function with typed params and a Google-style docstring whose
`Args:` block becomes the per-parameter description the model sees, e.g.:

```python
async def get_bureau_status(request_id: str) -> dict:
    """Get the per-bureau integration outcome (EQUIFAX, TRANSUNION) for a credit request:
    status (COMPLETED / FAILED / PENDING), whether a report was received, and any error_code.

    Args:
        request_id: The IR credit request id.
    """
    return await backend.get_bureau_status(request_id)
```

The agent registers them by reference — no separate schema file:

```python
# app/agent/investigator.py
investigator = LlmAgent(
    name="ir_credit_investigator",
    model=settings.llm_model,
    description="Investigates failed or stalled IR credit requests and proposes approved next steps.",
    instruction=load("investigator"),
    tools=[tools.get_credit_request, tools.get_bureau_status, tools.get_error_details,
           tools.search_knowledge_base, tools.propose_action],
)
```

**Q: How does a tool get access to per-session state (like the running list of citations), since the function signature is just business params?**

ADK injects a special `ToolContext` parameter when a tool declares it — it isn't part of the
generated schema, it's recognized by type and wired in at call time. `search_knowledge_base` and
`propose_action` both take it:

```python
async def search_knowledge_base(query: str, tool_context: ToolContext) -> dict:
    ...
    results = await retriever.search(query, k=4)
    cites = tool_context.state.get("citations", [])
    cites.extend({"chunk_id": r["chunk_id"], "source": r["source"], "score": r["score"]} for r in results)
    tool_context.state["citations"] = cites
    return {"results": results}
```

`get_credit_request`, `get_bureau_status`, and `get_error_details` don't need it — they're pure
reads with no side effect to record.

**Q: Why is there no `execute_retry` tool?**

Deliberately absent. The module docstring calls this out as an enforced design rule: "There is
NO execute-retry tool. `propose_action` only records intent; the workflow persists it as a
`PendingAction` and a human decides. The backend call happens in `app/workflows/actions.py`
after approval." `propose_action` itself only appends to session state and returns
`PENDING_HUMAN_APPROVAL` — it never calls the backend:

```python
async def propose_action(action_type: str, request_id: str, bureau: str, reason: str, tool_context: ToolContext) -> dict:
    if action_type != "RETRY_CREDIT_REQUEST":
        return {"accepted": False, "detail": "unsupported action_type"}
    proposals = tool_context.state.get("proposed_actions", [])
    proposals.append({"action_type": action_type, "target_id": request_id,
                      "parameters": {"bureau": bureau.upper()}, "reason": reason})
    tool_context.state["proposed_actions"] = proposals
    return {"accepted": True, "status": "PENDING_HUMAN_APPROVAL"}
```

**Q: How does the model know when to call `search_knowledge_base` vs. answer from its own knowledge?**

Two layers enforce it. The tool docstring instructs "Always cite the returned source in your
answer," and the system prompt (`app/prompts/investigator.md`) is explicit: "Never explain an
error code from memory — only from retrieved documentation," and later, "Never invent error
codes, statuses, or procedures. If the knowledge base has nothing for a code, say so and
recommend escalation." There's no code-level enforcement that the model actually calls the tool
before answering — that's a prompt-level guarantee, checked at eval time (see §6).

**Q: Is tool selection a fixed pipeline or does the model choose?**

Model-chosen — that's the stated reason for using ADK at all. The top of
`app/agent/investigator.py`: "Single reasoning agent: the model decides which tools to call and
in what order based on the question — that dynamic orchestration is the reason for ADK rather
than a fixed chain." There's exactly one `LlmAgent`; no `SequentialAgent`/`ParallelAgent`
composition is used.

---

## 2. Session state and the runner trace

**Q: Walk me through how a question turns into a tool-call trace and a final answer.**

`app/agent/runner.py` is the sole place that touches ADK's `Runner`/session types; everything
above it works with plain dicts. It creates a fresh `InMemorySessionService` session per
question, seeds its state, sends the user message, and folds the async event stream into a
trace:

```python
_sessions = InMemorySessionService()   # swap for DatabaseSessionService / VertexAiSessionService for multi-turn across replicas
_runner = Runner(agent=investigator, app_name=APP_NAME, session_service=_sessions)


async def investigate(question: str, user_id: str) -> dict:
    session_id = str(uuid.uuid4())
    await _sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id,
                                   state={"citations": [], "proposed_actions": []})
    msg = types.Content(role="user", parts=[types.Part(text=question)])

    trace: list[dict] = []
    final_text = ""
    async for event in _runner.run_async(user_id=user_id, session_id=session_id, new_message=msg):
        if not (event.content and event.content.parts):
            continue
        for part in event.content.parts:
            if part.function_call:
                trace.append({"type": "tool_call", "name": part.function_call.name,
                              "args": dict(part.function_call.args or {})})
            elif part.function_response:
                trace.append({"type": "tool_result", "name": part.function_response.name,
                              "result": part.function_response.response})
            elif part.text and event.is_final_response():
                final_text += part.text
```

It then re-fetches the session to pull out whatever the tools wrote into `state` during the run:

```python
    session = await _sessions.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    state = session.state if session else {}
    return {"answer": final_text.strip(), "trace": trace,
            "citations": state.get("citations", []), "proposed_actions": state.get("proposed_actions", [])}
```

So the trace comes from the **event stream** (every function call/response ADK emits), while
citations and proposed actions come from **session state** (what the tools explicitly wrote via
`ToolContext.state`) — two different mechanisms for two different purposes.

**Q: Why filter on `event.is_final_response()` for the text part?**

Because intermediate model turns can also contain `text` parts (e.g., reasoning alongside a
function call); only the final turn's text is the answer shown to the user. Only that text is
accumulated into `final_text`.

**Q: Is session state persisted, or does it disappear after the request?**

It's in-memory only, and the code says so out loud: `InMemorySessionService()` with the comment
"swap for `DatabaseSessionService` / `VertexAiSessionService` for multi-turn across replicas."
Today: one process, ephemeral session per investigation, no cross-request conversation memory.
Follow-up questions like "what about the Equifax side?" referencing a prior investigation are
**not built — planned** (the README's "Next steps" lists this explicitly).

**Q: What does `Investigation.tool_trace` in Postgres actually contain, and why?**

The exact `trace` list built above, stored as JSONB for audit:

```python
# app/db/models.py
tool_trace: Mapped[list] = mapped_column(JSONB, default=list)   # every tool call + response
```

It's the full call/response pairs (not just tool names), which is what lets the eval harness
recompute "which tools were called" (`tool_ok`) and feed raw tool results to an LLM judge as
ground truth (see §6).

---

## 3. The HITL approve/execute path

**Q: Walk through what happens between the agent proposing an action and the backend executing it.**

Three stages, each in a different module, each is a separate transaction/process boundary:

1. **Propose** (inside the agent run) — `tools.propose_action` appends to session state only,
   no persistence, no backend call (quoted in §1).

2. **Persist as PROPOSED** — after the run finishes, `app/workflows/investigation.py` turns each
   proposed action into a `PendingAction` row and sets the investigation's status:

```python
for p in result["proposed_actions"]:
    s.add(PendingAction(investigation_id=inv.id, action_type=p["action_type"], target_id=p["target_id"],
                        parameters=p["parameters"], agent_reason=p["reason"]))
inv.status = InvestigationStatus.AWAITING_APPROVAL if result["proposed_actions"] else InvestigationStatus.COMPLETED
```

3. **Approve → execute** — a human hits the decision endpoint; if `APPROVE`, the route schedules
   the actual backend call as a background task:

```python
# app/api/routes.py
@router.post("/actions/{action_id}/decision", response_model=schemas.ActionOut)
async def decide_action(action_id: uuid.UUID, body: schemas.ActionDecision, bg: BackgroundTasks, s: AsyncSession = Depends(get_session)):
    """HITL gate. A human approves or rejects; approval triggers execution through the backend service layer."""
    a = (await s.execute(select(PendingAction).where(PendingAction.id == action_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "action not found")
    if a.status != ActionStatus.PROPOSED:
        raise HTTPException(409, f"action already {a.status}")
    a.decided_by, a.decision_notes, a.decided_at = body.decided_by, body.notes, datetime.now(timezone.utc)
    a.status = ActionStatus.APPROVED if body.decision == "APPROVE" else ActionStatus.REJECTED
    await s.commit()
    if a.status == ActionStatus.APPROVED:
        bg.add_task(execute_approved_action, a.id)
    return schemas.ActionOut.model_validate(a, from_attributes=True)
```

`execute_approved_action` (`app/workflows/actions.py`) is, per its module docstring, "the ONLY
place a sensitive backend action is invoked, and only after a human approved it":

```python
async def execute_approved_action(action_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        a = (await s.execute(select(PendingAction).where(PendingAction.id == action_id))).scalar_one()
        if a.status != ActionStatus.APPROVED:
            return
        try:
            if a.action_type == "RETRY_CREDIT_REQUEST":
                bureau = a.parameters.get("bureau")
                a.execution_result = await backend.retry_credit_request(
                    a.target_id, approved_by=a.decided_by, bureau=None if bureau == "ALL" else bureau)
                a.status = ActionStatus.EXECUTED
            else:
                a.status, a.execution_result = ActionStatus.EXECUTION_FAILED, {"error": "unsupported action"}
        except Exception as e:  # noqa: BLE001
            log.exception("action execution failed id=%s", action_id)
            a.status, a.execution_result = ActionStatus.EXECUTION_FAILED, {"error": str(e)[:500]}
```

**Q: What stops a human approving the same action twice, or approving something already decided?**

The status check in `decide_action` before any mutation: `if a.status != ActionStatus.PROPOSED:
raise HTTPException(409, ...)`. And defense-in-depth inside the executor itself, which re-checks
before calling the backend: `if a.status != ActionStatus.APPROVED: return`.

**Q: How does the backend know the call is actually approved, not just an agent calling an API directly?**

There's no `execute` tool in the agent's toolset at all (§1), so the agent structurally cannot
reach this path. On top of that the mock backend enforces an explicit header:

```python
# mock_backend/main.py
@app.post("/credit-requests/{req_id}/retry")
def retry_credit_request(req_id: str, bureau: str | None = None, ...,
                         x_approved_by: str | None = Header(default=None)):
    """Sensitive action. The real service would enforce RBAC + audit; the mock enforces the approval header."""
    _auth(authorization)
    if not x_approved_by:
        raise HTTPException(403, "retry requires human approval (X-Approved-By)")
```

and the real client always sends it, sourced from the human decider, never the agent:

```python
# app/backend/client.py
async def retry_credit_request(self, request_id: str, approved_by: str, bureau: str | None = None) -> dict:
    """Only called by the workflow AFTER a human approves. Never exposed as an agent tool."""
    r = await self._client.post(f"/credit-requests/{request_id}/retry",
                                params={"bureau": bureau} if bureau else None,
                                headers={"X-Approved-By": approved_by})
```

The mock's own test locks this contract in: `test_retry_requires_approval_header` in
`tests/test_mock_backend.py` asserts a 403 without the header and 200 with it.

**Q: What is `X-Approved-By` in production — is there real authn/RBAC on who can approve?**

Not built — planned. `requested_by` / `decided_by` are free-text strings passed in the request
body (`schemas.InvestigationCreate.requested_by`, `schemas.ActionDecision.decided_by`), with a
comment noting the intent: `description="IR ops user id (from SSO in prod)"`. There's no auth
middleware, session, or RBAC check anywhere in `app/api/routes.py` — any caller can claim to be
any `decided_by`. That's explicitly the mock backend's job to simulate ("the real service would
enforce RBAC + audit; the mock enforces the approval header"), not this service's.

**Q: What happens if the backend call fails mid-execution — is there a retry/DLQ?**

No retry logic. The exception path in `execute_approved_action` catches broadly, truncates the
error, marks `EXECUTION_FAILED`, and commits — a human would have to notice and re-approve or
escalate manually. There's no dead-letter queue or automatic re-attempt.

---

## 4. RAG retrieval and citation grounding

**Q: How is the knowledge base chunked, and why heading-aware?**

`app/rag/chunker.py` first splits on `## `/`### ` headings so a single error code's whole
writeup stays intact, then packs paragraphs up to `max_chars` with overlap for continuity:

```python
def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    """Heading-aware: splits on '### ' / '## ' first so each error code stays in one chunk, then packs paragraphs."""
    sections, cur = [], ""
    for line in text.splitlines():
        if line.startswith(("## ", "### ")) and cur.strip():
            sections.append(cur.strip()); cur = ""
        cur += line + "\n"
    if cur.strip():
        sections.append(cur.strip())
    ...
```

This is directly tested: `tests/test_chunker.py::test_each_error_code_section_is_one_chunk`
asserts each of `TU-IDM-402`, `EQ-TMO-504`, `EQ-FRZ-301` lands in exactly one chunk, and that
chunk contains the word "Procedure" — i.e., the code and its remediation steps are never split
across chunks.

**Q: What embedding model, and how are query vs. document embeddings distinguished?**

`text-embedding-005` via Vertex, with the `task_type` param set differently for ingestion vs.
search:

```python
# app/rag/ingest.py
vectors = await embed(texts, task_type="RETRIEVAL_DOCUMENT")
```
```python
# app/rag/retriever.py
[qvec] = await embed([query], task_type="RETRIEVAL_QUERY")
```

Both funnel through the same `embed()` in `app/llm/vertex.py`, which calls
`client().models.embed_content(...)` with `output_dimensionality=settings.embedding_dim` (768,
matching the pgvector column).

**Q: How does retrieval actually run against Postgres?**

pgvector cosine distance, ordered ascending, converted to a similarity score:

```python
# app/rag/retriever.py
async def search(query: str, k: int = 4) -> list[dict]:
    [qvec] = await embed([query], task_type="RETRIEVAL_QUERY")
    distance = KnowledgeChunk.embedding.cosine_distance(qvec)
    stmt = select(KnowledgeChunk, distance.label("d")).order_by(distance).limit(k)
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).all()
    return [{"chunk_id": c.id, "source": c.source, "score": round(1.0 - float(d), 4), "content": c.content} for c, d in rows]
```

The column itself is `pgvector.sqlalchemy.Vector(settings.embedding_dim)` on `KnowledgeChunk`
(`app/db/models.py`), so retrieval is a plain SQL `ORDER BY <-> ... LIMIT k` under the
SQLAlchemy expression, no separate vector-DB service.

**Q: Is there a debug path to test retrieval quality without running the whole agent?**

Yes — `GET /api/v1/knowledge/search`:

```python
# app/api/routes.py
@router.get("/knowledge/search")
async def knowledge_search(q: str, k: int = 4):
    """Debug retrieval quality independent of the agent."""
    return await retriever.search(q, k=k)
```

**Q: How does citation grounding actually get enforced — is it just a prompt instruction?**

It's a combination of prompt instruction and state capture, not a hard code gate. The tool
docstring says "Always cite the returned source in your answer," and the system prompt requires
citing "the error code and the knowledge-base source in brackets, e.g.
`[ir_troubleshooting_guide.md]`." Mechanically, every call to `search_knowledge_base` appends
`{chunk_id, source, score}` into `tool_context.state["citations"]` (quoted in §1), which the
runner surfaces as `result["citations"]` and the API exposes as `InvestigationOut.citations`. So
the system *records* what was retrieved, and *instructs* the model to ground its answer in it —
but nothing parses the model's final text to verify every claim actually maps to a citation.
That check exists only in the eval harness's `grounded` metric (see §6), which is a coarse proxy
("citations non-empty when error codes were expected"), not sentence-level attribution.

**Q: How do you ingest new docs, and what happens to old chunks of the same file on re-ingest?**

`python -m app.rag.ingest data/knowledge/*.md`. Per file, existing chunks for that `source` are
deleted before inserting the new ones, batched 32 at a time:

```python
# app/rag/ingest.py
await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source == path.name))
for i in range(0, len(chunks), batch):
    texts = chunks[i:i + batch]
    vectors = await embed(texts, task_type="RETRIEVAL_DOCUMENT")
    session.add_all(...)
```

So re-ingesting the same filename is a clean replace, not an append — no duplicate/stale chunks
for a source that was already ingested.

**Q: Can you show retrieval actually working, not just describe it?**

Yes — this was run live against the real stack (local Postgres 17 + pgvector, real Vertex AI
embeddings via ADC), not mocked. First, ingestion:

```
$ python -m app.rag.ingest data/knowledge/ir_troubleshooting_guide.md
ir_troubleshooting_guide.md: 11 chunks
```

Then three real calls to the debug endpoint (`GET /api/v1/knowledge/search`, §4 above):

**Exact error code query** — `q=TU-IDM-402`:
```json
[
  {
    "chunk_id": 16, "source": "ir_troubleshooting_guide.md", "score": 0.6812,
    "content": "### TU-IDM-402 — TransUnion identifying information mismatch\nMeaning: TransUnion could not match the borrower's name, address, or date of birth to a consumer file.\nRetryable: Yes, but only after the borrower's identifying information has been verified with the lender.\nProcedure: 1) Confirm the borrower reference on the request matches the lender's submission. 2) Ask the lender to verify name, current address, and date of birth. 3) After correction, retry the TransUnion pull only — do not re-pull Equifax if it already completed."
  },
  { "chunk_id": 17, "source": "ir_troubleshooting_guide.md", "score": 0.6669,
    "content": "### EQ-IDM-402 — Equifax identifying information mismatch\nMeaning and procedure identical to TU-IDM-402, applied to the Equifax pull." },
  { "chunk_id": 18, "source": "ir_troubleshooting_guide.md", "score": 0.5637,
    "content": "### EQ-TMO-504 / TU-TMO-504 — Bureau gateway timeout\n..." }
]
```
Top hit is the exact code, with its full "Meaning / Retryable / Procedure" block intact in one
chunk (the heading-aware chunker at work) — and the near-neighbors it surfaced (`EQ-IDM-402`,
the timeout code) are genuinely related error families, not noise.

**Paraphrased natural-language query, no error code at all** — `q=bureau gateway timeout retry policy`:
```json
[
  { "chunk_id": 18, "score": 0.7245,
    "content": "### EQ-TMO-504 / TU-TMO-504 — Bureau gateway timeout\nMeaning: The bureau did not respond within IR's 30-second timeout. Usually transient.\nRetryable: Yes.\nProcedure: 1) Check the bureau status page for a known outage. 2) If no outage, retry immediately. 3) If a second timeout occurs, wait 15 minutes before the next retry. 4) If the retry count has reached 3, escalate to the integrations team rather than retrying again." },
  { "chunk_id": 14, "score": 0.7112,
    "content": "## Retry policy\nA request may be retried at most 3 times in total. Retries are a sensitive operation because each bureau pull may be billed and may be recorded on the consumer's file. Retries must be approved by an authorized IR operations user. Never retry a request whose error is marked non-retryable." },
  { "chunk_id": 21, "score": 0.6961,
    "content": "## Pending status\nA bureau pull in PENDING status for less than 5 minutes is normal. ..." }
]
```
Top match is still the right timeout section despite zero literal keyword overlap with an error
code — pure semantic match. It also correctly pulled in the general "Retry policy" section
(3-retry cap, requires human approval) even though that chunk never says "timeout."

**"frozen consumer file"** (tests the *must-not-retry* case):
```json
[
  { "chunk_id": 19, "score": 0.6583,
    "content": "### EQ-FRZ-301 / TU-FRZ-301 — Consumer file frozen\nMeaning: The consumer has placed a security freeze on their file. The bureau will not release a report.\nRetryable: No. Retrying will fail again and may generate a billable inquiry.\nProcedure: Notify the lender that the borrower must temporarily lift the freeze with the bureau. Do not retry until the lender confirms the freeze has been lifted." },
  { "chunk_id": 14, "score": 0.515, "content": "## Retry policy\n..." }
]
```
Correctly ranks the frozen-file section first, with `"Retryable": No` visible in the retrieved
text — this is the chunk the agent's prompt relies on to refuse a retry for request `12347`
(§3, §6 golden case `e03`).

This demonstrates the chunker/embedding/retrieval pipeline end-to-end with real infra, not a
described-but-unverified design.

---

## 5. Config and auth to Vertex AI

**Q: How does the service authenticate to Vertex AI — API keys?**

No API key. `app/llm/vertex.py`'s module docstring states it plainly: "Auth is ADC: `gcloud auth
application-default login` locally, Workload Identity on GKE." The `genai.Client` is constructed
with `vertexai=True` and project/location from settings, which is what routes the SDK through
Vertex + Application Default Credentials rather than the Gemini Developer API:

```python
@functools.lru_cache(maxsize=1)
def client() -> genai.Client:
    return genai.Client(vertexai=settings.google_genai_use_vertexai,
                        project=settings.google_cloud_project or None,
                        location=settings.google_cloud_location)
```

`k8s/agent.yaml` confirms the production side: no credential file or key env var, just
`serviceAccountName: ir-credit-agent-sa   # Workload Identity -> GSA with aiplatform.user, ...`.

**Q: The Google ADK agent presumably needs its own client — how does it pick up the same Vertex config?**

This is called out directly in `app/config.py`, because it's a real gotcha: ADK builds its own
genai client from process environment variables, not from the app's `Settings` object, so
`config.py` mirrors the relevant settings into `os.environ` right after constructing `settings`:

```python
settings = Settings()

# Google ADK builds its own genai client from the process environment, not from this object.
# Export the Vertex settings so ADK uses Vertex AI (ADC) instead of looking for an API key.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE" if settings.google_genai_use_vertexai else "FALSE")
if settings.google_cloud_project:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.google_cloud_location)
```

Using `setdefault` rather than a hard assignment means an operator can still override via real
env vars without `config.py` clobbering them.

**Q: Sync SDK calls inside an async FastAPI service — how is that handled?**

`google.genai`'s `models.embed_content` / `generate_content` are blocking calls; both are wrapped
in `asyncio.to_thread` so they don't block the event loop:

```python
async def embed(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    def _call():
        resp = client().models.embed_content(...)
        return [e.values for e in resp.embeddings]
    return await asyncio.to_thread(_call)
```

Same pattern in `generate()`.

**Q: Where does `generate()` (plain non-agentic generation) actually get used?**

Only by the eval harness's LLM-judge step (`evals/run_evals.py` calls
`app.llm.vertex.generate(..., json_mode=True)` to score answers) — the investigator agent itself
never calls it directly; ADK's `LlmAgent`/`Runner` manage generation internally.

**Q: How are prompts managed — inline strings, or something more structured?**

Versioned markdown files loaded through a tiny registry, explicitly framed as a governance point:
"Prompt registry: versioned markdown in `app/prompts/`. A prompt change is a PR gated by evals."

```python
# app/llm/prompts.py
PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

@lru_cache(maxsize=None)
def load(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
```

Today there's exactly one prompt, `app/prompts/investigator.md`, loaded once via
`load("investigator")` and cached for the process lifetime — no runtime hot-reload, no prompt
A/B mechanism beyond swapping the file and re-deploying.

**Q: What other config knobs exist, and what are the defaults?**

All in `app/config.py`'s `Settings` (pydantic-settings, reads `.env`, ignores unknown keys):
`google_cloud_project`, `google_cloud_location` (default `us-central1`),
`google_genai_use_vertexai` (default `True`), `llm_model` (default `gemini-2.5-flash`; note the
K8s ConfigMap pins `gemini-2.0-flash` instead — an actual drift between the code default and the
deploy manifest), `embedding_model` (`text-embedding-005`), `embedding_dim` (768),
`database_url`, `ir_backend_url`/`ir_backend_token` (dev defaults point at `localhost`), `app_env`
(default `local`, used to gate Cloud Logging/Trace exporters on/off), `log_level`.

**Q: How is observability wired for local vs. GCP?**

`app/observability.py`: logging always goes to stdout via `logging.basicConfig`; Cloud Logging is
only attempted when `app_env != "local"`, and failure to init it is caught and logged as a
warning rather than crashing the app. Tracing is OpenTelemetry + FastAPI auto-instrumentation
locally; the Cloud Trace exporter is only added when `app_env != "local"`, and the whole function
is wrapped in a broad `try/except` so a tracing misconfiguration never takes down the service —
it just logs "tracing not configured."

---

## 6. Test coverage

**Q: What's actually covered by `pytest`, and what isn't?**

Three files, each narrow and fast — no network, no live model calls, no live Postgres:

1. `tests/test_action_schema.py` — validates the `ActionDecision` pydantic model rejects any
   `decision` other than `APPROVE`/`REJECT`:
   ```python
   def test_only_approve_or_reject():
       with pytest.raises(ValidationError):
           ActionDecision(decided_by="ops", decision="MAYBE")
       assert ActionDecision(decided_by="ops", decision="REJECT").decision == "REJECT"
   ```

2. `tests/test_chunker.py` — the RAG chunking invariant described in §4 (one chunk per error
   code, procedure text preserved).

3. `tests/test_mock_backend.py` — exercises the mock IR backend via `fastapi.testclient
   .TestClient` (in-process, no server needed): 200 + correct status for a known request, 404 for
   an unknown one, 401 with no bearer token, and the approval-header contract on retry (403
   without `X-Approved-By`, 200 with it).

`pytest.ini` sets `asyncio_mode = auto`, so async test functions run without extra decorators.

**Q: Is the actual agent — tool orchestration, the FastAPI routes, the HITL workflow, RAG
retrieval end-to-end — covered by this test suite?**

No. Not built — planned, as far as `pytest` goes. There's no test that spins up the real
`investigator` agent, hits `/api/v1/investigations`, drives an investigation through
`AWAITING_APPROVAL` → `/actions/{id}/decision` → `EXECUTED`, or calls `retriever.search` against a
seeded pgvector table. Those integration paths are covered by a separate mechanism — the eval
harness — not by `pytest`.

**Q: So how is the end-to-end agent behavior actually verified?**

`evals/run_evals.py`, run against a live stack (Vertex + DB + mock backend), not mocked — its
own docstring: "run against live Vertex + DB + mock backend." It replays six golden cases from
`evals/golden_cases.jsonl` (seeded scenarios: TU identity mismatch → retry TU only; double
timeout at retry limit → retry ALL; frozen file → must NOT retry; in-progress; success; unknown
request), and for each computes five signals:

```python
called = {t["name"] for t in out["trace"] if t["type"] == "tool_call"}
proposed = out["proposed_actions"][0] if out["proposed_actions"] else None
action_ok = (proposed is None and c["expect_action"] is None) or (
    proposed is not None and proposed["action_type"] == c["expect_action"]
    and proposed["parameters"].get("bureau") == c["expect_bureau"])
ground_truth = json.dumps([t for t in out["trace"] if t["type"] == "tool_result"])[:6000]
judge = json.loads(await generate(JUDGE.format(trace=ground_truth, answer=out["answer"]), json_mode=True))
r = {"id": c["id"],
     "tool_ok": set(c["expect_tools"]) <= called,
     "codes_ok": all(code in out["answer"] for code in c["expect_codes"]),
     "action_ok": action_ok,
     "grounded": not c["expect_codes"] or bool(out["citations"]),
     "judge_score": judge.get("score", 0), "issues": judge.get("issues", [])}
```

`judge_score` comes from a second LLM call (`generate(..., json_mode=True)`) grading the answer
1-5 against the actual tool-call ground truth for: every claim supported by tool results, no
borrower PII, no invented procedure, concision.

**Q: What gates a prompt or model change from shipping if it regresses behavior?**

The CLI exit code, driven by configurable thresholds — and action-gating is held to 100%,
everything else has slack:

```python
ap.add_argument("--min-action", type=float, default=1.0, help="action gating must be perfect")
ap.add_argument("--min-codes", type=float, default=0.8)
ap.add_argument("--min-judge", type=float, default=3.5)
...
ok = m["action_rate"] >= a.min_action and m["codes_rate"] >= a.min_codes and m["grounding_rate"] == 1.0 and m["avg_judge"] >= a.min_judge
sys.exit(0 if ok else 1)
```

`README.md` frames this as the actual CI gate ("HITL + LLM evals... as a CI gate") and the
process for growing the suite: "Add a golden case every time an ops user disagrees with the
agent — that becomes your regression suite." Whether that's actually wired into a CI pipeline
(GitHub Actions, Cloud Build trigger, etc.) is **not built — planned**: `cloudbuild.yaml` exists
for image build/deploy, but I found no step in it or elsewhere that invokes
`python -m evals.run_evals` automatically on a PR.

**Q: Any load, concurrency, or failure-injection testing (e.g., backend timeout, DB down)?**

Not built — planned. No tests exercise `httpx` timeouts, partial DB outages, or concurrent
approval races beyond the single status-check guard described in §3.

---

## 7. Tool wiring mechanics, tool-call stop conditions, and MCP vs. native tools

**Q: Concretely, how does the orchestration layer (ADK) connect to and call your tools?**

Entirely declaratively — you hand `LlmAgent` a list of plain Python functions, and ADK does
schema generation, invocation, and response-feeding at runtime. No manual registry or adapter
classes:

```python
# app/agent/investigator.py
investigator = LlmAgent(
    name="ir_credit_investigator",
    model=settings.llm_model,
    instruction=load("investigator"),
    tools=[tools.get_credit_request, tools.get_bureau_status, tools.get_error_details,
           tools.search_knowledge_base, tools.propose_action],
)
```

The chain at call time:
1. **Schema generation** — ADK introspects each function's name, type-hinted params, and
   docstring `Args:` block to build the JSON schema the model sees as a callable function
   (`app/agent/tools.py`: *"Docstrings + type hints become the schema the model sees."*).
2. **Dispatch** — `Runner.run_async` (`app/agent/runner.py`) drives the loop. When the model's
   response contains a function call, ADK matches the function name back to the actual Python
   callable in the `tools` list, deserializes the model-generated arguments into that function's
   parameters, and **injects `ToolContext` automatically** if the function signature declares it
   (`search_knowledge_base(query: str, tool_context: ToolContext)`) — this is how a tool reaches
   session state without it being part of the model-visible schema.
3. **Result feedback** — ADK awaits the coroutine, takes the return dict, and feeds it back to
   the model as a `function_response`, continuing the loop.
4. **Observation only, not control** — `app/agent/runner.py` never calls a tool itself; it only
   listens to the event stream ADK emits and records it:
   ```python
   if part.function_call:
       trace.append({"type": "tool_call", "name": part.function_call.name, "args": dict(part.function_call.args or {})})
   elif part.function_response:
       trace.append({"type": "tool_result", "name": part.function_response.name, "result": part.function_response.response})
   ```

So: you provide functions → ADK turns them into callable schemas → `Runner` executes the Python
call when the model requests it → the loop repeats until the model stops calling tools. All
orchestration decisions (which tool, what args, when to stop) are made by the model, not by code
in this repo. There is no separate "tool server" or protocol hop — everything runs in-process.

**Q: How does the model decide when to stop calling tools?**

It's a per-turn model decision — there is no loop-counter or max-tool-calls setting anywhere in
this repo's code. On each turn, the model's response either contains `function_call` parts (ADK
executes them and loops again) or is text-only, which ADK/Gemini treats as the final turn:

```python
# app/agent/runner.py
elif part.text and event.is_final_response():
    final_text += part.text
```

`event.is_final_response()` is just detecting "this turn had no function call." There's no
explicit separate "should I stop" decision point in the code — stopping falls out naturally from
the model choosing to emit plain text instead of another function call.

What actually *steers* the model toward stopping at the right point is entirely the system
prompt (`app/prompts/investigator.md`), which lays out a numbered procedure ending in an explicit
instruction to finish:
```
1. Identify the credit request id... If none is given, ask for it and stop.
2. Call get_credit_request...
3. Call get_bureau_status...
4. If any bureau failed or errored, call get_error_details.
5. For every error code you find, call search_knowledge_base...
6. Decide whether a retry is appropriate... If appropriate, call propose_action...
7. Write the final answer.
```
Step 1 gives an early-exit path (missing request id → ask and stop, no tools called at all); step
7 is the "stop calling tools, answer now" instruction.

**Q: Is "did the model stop at the right point" verified anywhere?**

Only indirectly. `evals/run_evals.py`'s `tool_ok` metric checks tool **coverage**
(`set(expect_tools) <= called` — were the required tools called), not that the model stopped
appropriately or didn't over-call. There's no check today that would catch, say, the model
calling `search_knowledge_base` five redundant times, or continuing to call tools past when it
had enough information. **Not built — planned.**

**Q: In this project vs. real production agent systems, how does the stop condition differ?**

In this repo, the stop condition is exactly what's described above: a single un-bounded
model-decides-per-turn loop, guided only by prompt instructions, with no code-level safety net.
That's a reasonable scope for a project with one agent, five tools, and a narrow domain — but it
is thinner than what you'd want at production scale. Speaking generally (this part is *not*
describing this repo's code, since none of it exists here):

- **Max-iteration / max-tool-call ceilings.** Production agent frameworks (and ADK itself, at
  the library level, separate from how this repo configures it) typically enforce a hard cap on
  turns/tool-calls per run, so a model that gets stuck in a call-tool-reread-repeat pattern
  can't loop indefinitely or run up cost. This repo has no such cap configured on `LlmAgent`/
  `Runner`.
- **Deterministic step gating for critical paths.** Instead of trusting the prompt's 7-step list
  to always be followed, a more defensive production design might hard-code that certain tools
  are mandatory before the model is allowed to propose an action (e.g., refuse to persist a
  `PendingAction` unless the trace shows `get_error_details` and `search_knowledge_base` were
  actually called) — i.e., verify the stop *decision* in code, not just prompt it. Here, that
  check only exists as an eval-time coverage metric, not a runtime guard.
- **Cost/latency budgets as a stop signal.** Production systems often stop a run early on a
  token or wall-clock budget, independent of whether the model "wants" to keep going. Nothing
  like that exists here — a single `investigate()` call runs to whatever length the model and
  prompt naturally produce.
- **Structured "done" signaling.** Some production agents have the model emit an explicit
  terminal tool call (e.g. `finish_investigation(...)`) rather than relying on "no function call
  in this turn = done," which makes the stop condition explicit and inspectable rather than
  implicit in the absence of a function call. This repo relies on the implicit signal.

The gap is intentional-by-omission rather than a documented tradeoff — the repo doesn't discuss
it — but it's worth naming plainly to an interviewer: this project demonstrates *dynamic tool
orchestration*, not *hardened production guardrails around that orchestration*.

**Q: Why didn't the project expose tools via MCP (Model Context Protocol) instead of ADK's native tool mechanism?**

There's no mention of MCP anywhere in the repo — no code, docs, or dependency references it
(`requirements.txt` has no MCP package). So there's no documented rationale to quote; this is a
design choice the repo doesn't discuss. The honest comparison, reasoning from the architecture
that *is* here:

**What was actually built — ADK native tools (in-process functions):**
```python
tools=[tools.get_credit_request, tools.get_bureau_status, tools.get_error_details,
       tools.search_knowledge_base, tools.propose_action]
```
Plain async Python functions in the same process as the agent, calling your own `httpx` client
(`app/backend/client.py`) or your own DB-backed retriever (`app/rag/retriever.py`) directly. ADK
generates the schema from the function signature and invokes it in-process — no separate server,
no protocol, no network hop between agent and tool.

**What MCP would add:** a standard client-server protocol so tools can live in a separate
process/service (possibly a different language), be reused across multiple agents or agent
frameworks (not just ADK), and be exposed by a third party without you writing framework-specific
bindings.

**Why that tradeoff doesn't pay off here, given what's actually in this repo:**
- **Single consumer.** There is exactly one agent (`investigator`) and no second framework or
  team that would reuse these tool definitions — MCP's cross-framework reusability has no
  current beneficiary.
- **The reusable boundary already exists at a different layer.** `app/backend/client.py`'s
  docstring: *"every read/write goes through these approved endpoints, so authz, auditing, and
  data filtering stay in the enterprise application."* The IR backend's HTTP API is already the
  interoperable contract; putting MCP in front of it would be a second protocol wrapping the same
  boundary, adding a hop without adding capability.
- **Lower friction for what's actually tested here.** ADK-native tools are plain functions you
  can unit-test directly and reason about locally; MCP would mean standing up and operating a
  separate server process, defining its manifest, and managing its lifecycle in
  `docker-compose.yml`/`k8s/` for no functional gain at this scale.
- **`ToolContext` has no MCP equivalent used here.** Session-state access for citations/proposed
  actions (§1, §2) is an ADK-native mechanism. Routing tool calls through MCP would require
  designing your own way to carry session state across the protocol boundary, since MCP's
  tool-call contract doesn't include this by default.

**When the tradeoff would flip:** if the same tools (`get_credit_request`,
`search_knowledge_base`, etc.) needed to be callable from a different agent framework, a
different team's agent, or a general MCP-compatible client (IDE, Claude Desktop, etc.) — that is
exactly the case MCP solves. With a single ADK agent and no other consumer, MCP would be
indirection without payoff today.

**Q: Conceptually, is this "ADK vs. MCP" — are they competing choices?**

No — they solve different problems and aren't mutually exclusive at the framework level. ADK is
an **agent-building framework** (agent definition, session/runner, tool orchestration loop); MCP
is a **protocol** for exposing tools/resources to any LLM client over a standard wire format
(JSON-RPC, stdio or HTTP/SSE), independent of what framework built the agent. ADK can consume
MCP-exposed tools via its own MCP toolset adapter just as it consumes native Python functions —
so the real decision isn't "ADK or MCP," it's **where a given tool lives**: as a plain function
in the agent's own process (ADK-native, what this repo does), or as a separate MCP server any
agent can call into.

| | ADK (as used here) | MCP |
|---|---|---|
| What it is | Framework: `LlmAgent`, `Runner`, sessions, tool-call loop | Protocol: standard tool/resource exposure over JSON-RPC |
| Tool definition | Python function + docstring/type hints → ADK auto-generates schema | Tool schema is part of the wire protocol, server-defined |
| Where the tool runs | In-process with the agent (`app/agent/tools.py`) | Separate process/service, any language |
| Reusability | Within this ADK agent/process only | Across any MCP-compatible client — ADK, other frameworks, IDEs |
| Session/state passing | Native — `ToolContext.state` (§1, §2) | Not built-in — you'd design your own convention |
| Overhead per call | A function call | A process boundary + serialization |

This repo picked ADK-native tools, not because MCP is inferior, but because there is exactly one
consumer (`investigator`) and the interoperable boundary already lives one layer down, at the IR
backend's own HTTP API (`app/backend/client.py`). If a second agent or team ever needed to call
these same tools, that's the point at which wrapping them as an MCP server would earn its keep.

**Q: If the IR backend already had exposed API endpoints and you wanted to connect the agent to
it via MCP instead, what would that look like in code?**

Illustrative sample only — not part of this repo (§7 confirms MCP isn't referenced anywhere in
the codebase), and `google-adk`'s MCP integration class names have moved across releases, so
treat exact imports as "verify against your installed version." The shape: instead of the agent
process calling the IR backend's endpoints directly via `httpx` (as `app/backend/client.py` does
today), a small MCP server wraps those same endpoints, and the agent connects to *that* server
instead.

**1. An MCP server wrapping the existing backend's endpoints** — same docstrings as
`app/agent/tools.py`, but the function body now lives in its own process:

```python
# mcp_server/ir_backend_server.py
"""MCP server exposing the existing IR backend's REST endpoints as MCP tools.
Any MCP-compatible agent — not just this project's — can connect to it."""
import httpx
from mcp.server.fastmcp import FastMCP

BACKEND_URL, BACKEND_TOKEN = "http://localhost:9000", "dev-token"
mcp = FastMCP("ir-backend")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BACKEND_URL,
                              headers={"Authorization": f"Bearer {BACKEND_TOKEN}"}, timeout=10.0)


@mcp.tool()
async def get_credit_request(request_id: str) -> dict:
    """Get an IR credit request: lender, loan purpose, requested bureaus, overall IR status, retry_count."""
    async with _client() as c:
        r = await c.get(f"/credit-requests/{request_id}")
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def get_bureau_status(request_id: str) -> dict:
    """Get the per-bureau integration outcome (EQUIFAX, TRANSUNION) for a credit request."""
    async with _client() as c:
        r = await c.get(f"/credit-requests/{request_id}/bureaus")
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def get_error_details(request_id: str) -> dict:
    """Get the error records for a credit request: bureau, error code, message, retryable."""
    async with _client() as c:
        r = await c.get(f"/credit-requests/{request_id}/errors")
        r.raise_for_status()
        return r.json()


# Deliberately NOT exposed here: retry_credit_request — same rule as app/backend/client.py,
# sensitive actions are never agent-callable tools.

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8090)  # network-reachable, since the backend is a separate existing system
```

**2. The agent side — `MCPToolset` in place of the direct import:**

```python
# app/agent/investigator.py  (MCP variant)
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from app.agent import tools          # local tools that stay local — see caveat below
from app.config import settings
from app.llm.prompts import load

ir_backend_tools = MCPToolset(
    connection_params=StreamableHTTPConnectionParams(url="http://localhost:8090/mcp"),
    tool_filter=["get_credit_request", "get_bureau_status", "get_error_details"],  # optional allowlist
)

investigator = LlmAgent(
    name="ir_credit_investigator",
    model=settings.llm_model,
    description="Investigates failed or stalled IR credit requests and proposes approved next steps.",
    instruction=load("investigator"),
    tools=[ir_backend_tools, tools.search_knowledge_base, tools.propose_action],
)
```

Everything else — `Runner`, session handling, the prompt, the HITL workflow — is unchanged. ADK
treats an `MCPToolset`'s discovered tools the same as native Python functions when building the
model's schema; the agent doesn't know or care that `get_credit_request` now runs over the
network instead of in-process.

**Why `search_knowledge_base` and `propose_action` would stay local, not move to MCP:** both use
`ToolContext.state` to read/write session state (citations, proposed actions — §1, §2). MCP's
tool-call contract has no equivalent for that — an MCP tool only gets its declared args and
returns a plain result, with no session handle. Moving those two over MCP would require
redesigning how citations/proposals get threaded back into the agent's session (e.g., the tool
returns the citation data in its response payload, and a wrapper on the ADK side lifts it into
state, instead of the tool writing to state directly). That's the concrete, code-level version of
the "`ToolContext` has no MCP equivalent" tradeoff already named above — it's not free to move a
stateful tool to MCP, only a stateless read is a drop-in swap.

---

## Summary table: what's built vs. planned

| Area | Built | Not built — planned |
|---|---|---|
| Tool schemas | Auto-generated from type hints + docstrings (`app/agent/tools.py`) | — |
| Dynamic orchestration | Single `LlmAgent`, model picks tool order | Multi-agent decomposition |
| Session state | `InMemorySessionService`, per-question session | Multi-turn/follow-up across requests (`DatabaseSessionService`/`VertexAiSessionService`) |
| HITL gate | `PendingAction` + `/actions/{id}/decision`, status-guarded, header-enforced on backend | Real authn/RBAC on `decided_by` / `requested_by` (currently free-text) |
| RAG | pgvector cosine search, heading-aware chunking, debug endpoint | Sentence-level citation verification (only coarse "citations non-empty" check) |
| Vertex auth | ADC / Workload Identity, env mirrored for ADK | — |
| Background execution | FastAPI `BackgroundTasks` | Durable queue (Cloud Tasks/Pub-Sub, per README) |
| Unit tests | Schema validation, chunker invariant, mock backend contract | Agent/API/workflow integration tests in `pytest` |
| Agent behavior verification | `evals/run_evals.py` against live stack, golden cases, LLM-as-judge | Automatic CI wiring of the evals gate; load/failure-injection tests |
| Migrations | `Base.metadata.create_all` on startup | Alembic (README says so directly) |
| Observability | Structured logging + OpenTelemetry tracing, GCP-conditional | Per-tool spans, token/latency metrics per investigation (README "Next steps") |
| Tool-call stop condition | Implicit: model emits no more `function_call` parts → `is_final_response()` (`app/agent/runner.py`), guided only by the prompt's 7-step procedure | Max-iteration/tool-call ceiling, code-level step gating, cost/latency budget, explicit "done" tool call |
| Tool protocol | ADK native in-process tools — functions registered directly on `LlmAgent` (`app/agent/investigator.py`) | MCP (or any cross-framework tool server) — not referenced anywhere in the repo; would only pay off with a second agent/framework consumer |
