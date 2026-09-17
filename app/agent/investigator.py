"""
Single reasoning agent: the model decides which tools to call and in what order based on the
question — that dynamic orchestration is the reason for ADK rather than a fixed chain.
"""
from google.adk.agents import LlmAgent

from app.agent import tools
from app.config import settings
from app.llm.prompts import load
# ir_backend_tools = MCPToolset(
#     connection_params=StreamableHTTPConnectionParams(url="http://localhost:8090/mcp"),
#     tool_filter=["get_credit_request", "get_bureau_status", "get_error_details"],  # optional allowlist
# )

# investigator = LlmAgent(
#     name="ir_credit_investigator",
#     model=settings.llm_model,
#     description="Investigates failed or stalled IR credit requests and proposes approved next steps.",
#     instruction=load("investigator"),
#     tools=[ir_backend_tools, tools.search_knowledge_base, tools.propose_action],
# )
investigator = LlmAgent(
    name="ir_credit_investigator",
    model=settings.llm_model,
    description="Investigates failed or stalled IR credit requests and proposes approved next steps.",
    instruction=load("investigator"),
    tools=[tools.get_credit_request, tools.get_bureau_status, tools.get_error_details,
           tools.search_knowledge_base, tools.propose_action],
)
