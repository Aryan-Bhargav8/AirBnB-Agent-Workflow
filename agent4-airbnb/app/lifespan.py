from contextlib import asynccontextmanager

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver

from app.agent import mcp_config

# Module-level singletons — initialised in lifespan, used by agent.py per request
mcp_client: MultiServerMCPClient | None = None
checkpointer: MemorySaver | None = None


@asynccontextmanager
async def lifespan(app):
    global mcp_client, checkpointer
    mcp_client = MultiServerMCPClient(mcp_config)
    checkpointer = MemorySaver()
    yield
