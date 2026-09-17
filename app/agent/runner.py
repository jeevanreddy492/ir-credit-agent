"""Runs the investigator for one question and returns answer + trace + state. Isolates ADK types from the workflow."""
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent.investigator import investigator

APP_NAME = "ir-credit-agent"
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

    session = await _sessions.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    state = session.state if session else {}
    return {"answer": final_text.strip(), "trace": trace,
            "citations": state.get("citations", []), "proposed_actions": state.get("proposed_actions", [])}
