from pydantic import BaseModel
from langchain_core.messages import AnyMessage
from typing import Annotated
from langgraph.graph.message import add_messages


def _replace_listings(old: list[dict], new: list[dict]) -> list[dict]:
    """Reducer that fully replaces the listings list.
    Called when extractor_node writes its results into state."""
    return new


class AgentState(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages]
    listings: Annotated[list[dict], _replace_listings] = []