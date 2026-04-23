import os
import re
import sys
import json

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import ToolNode, tools_condition

from app.tools.airbnb_tool import get_places_id
from app.model.agent_model import AgentState
from app.propmt import SYSTEM_PROMPT, AGENTS_MD

mcp_config = {
    "airbnb": {
        "command": "npx.cmd" if sys.platform == "win32" else "npx",
        "args": ["-y", "@openbnb/mcp-server-airbnb", "--ignore-robots-txt"],
        "transport": "stdio",
        "env": dict(os.environ),
    }
}


def _to_text(content) -> str:
    """MCP tool messages return content as a list of block dicts;
    extract the actual text rather than repr()-ing the whole list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return next(
            (b["text"] for b in content if isinstance(b, dict) and "text" in b),
            "",
        )
    return str(content)


def _extract_listings(search_blob: str, detail_blobs: list[str]) -> list[dict]:
    """Pure-Python extraction from airbnb_search + airbnb_listing_details payloads.

    Search result fields (predictable JSON paths):
      id, name, url, price, rating, reviewsCount, beds, badges

    Detail blobs contribute the date-stamped listingUrl, matched by listing ID.
    No LLM call — avoids token-limit crashes on large result sets.
    """
    # Build id → dated URL map from detail blobs
    url_by_id: dict[str, str] = {}
    for blob in detail_blobs:
        try:
            data = json.loads(blob)
            listing_url = data.get("listingUrl", "")
            m = re.search(r"/rooms/(\d+)", listing_url)
            if m:
                url_by_id[m.group(1)] = listing_url
        except Exception:
            continue

    try:
        search_data = json.loads(search_blob)
    except Exception:
        return []

    results = search_data.get("searchResults", [])
    if not results and isinstance(search_data, list):
        results = search_data

    rows: list[dict] = []
    for item in results:
        lid = str(item.get("id", ""))

        # Name
        try:
            name = item["demandStayListing"]["description"]["name"][
                "localizedStringWithTranslationPreference"
            ]
        except (KeyError, TypeError):
            name = ""

        # URL — prefer date-stamped URL from the details blob
        url = url_by_id.get(lid, item.get("url", ""))

        # Rating + reviewsCount from "4.96 out of 5 average rating, 282 reviews"
        rating_label = item.get("avgRatingA11yLabel", "")
        rm = re.search(r"([\d.]+)\s+out of 5", rating_label)
        rating = rm.group(1) if rm else ""
        rcm = re.search(r"([\d,]+)\s+reviews?", rating_label)
        reviewsCount = rcm.group(1).replace(",", "") if rcm else ""

        # Beds from "2 bedrooms, 4 beds" or "2 king beds"
        primary_line = item.get("structuredContent", {}).get("primaryLine", "")
        bm = re.search(r"(\d+)\s+(?:king\s+)?beds?", primary_line)
        beds = bm.group(1) if bm else ""

        # Price from priceDetails: "7 nights x ₹34,945.16: ₹2,44,616.11"
        price = ""
        try:
            price_details = item["structuredDisplayPrice"]["explanationData"]["priceDetails"]
            pm = re.search(r"x\s*([^:]+?):", price_details)
            if pm:
                price = pm.group(1).strip() + " / night"
        except (KeyError, TypeError):
            pass
        if not price:
            try:
                price = item["structuredDisplayPrice"]["primaryLine"]["accessibilityLabel"]
            except (KeyError, TypeError):
                pass

        badges = item.get("badges", "")

        rows.append({
            "id": lid,
            "name": name,
            "url": url,
            "price": price,
            "rating": rating,
            "reviewsCount": reviewsCount,
            "beds": beds,
            "badges": badges,
        })

    return rows


def _route_after_tools(state: AgentState) -> str:
    """After ToolNode: route to extractor once listing details are done,
    stay in llm after airbnb_search so the agent fetches per-listing details.
    """
    for msg in reversed(state.messages):
        if isinstance(msg, ToolMessage):
            if msg.name and "airbnb_listing_details" in msg.name:
                return "extractor"
            if msg.name and "airbnb_search" in msg.name:
                return "llm"
        else:
            break
    return "llm"


def build_graph(tools: list, checkpointer):
    llm = ChatAnthropic(
        base_url="http://host.docker.internal:11434",
        api_key="ollama",
        model="minimax-m2.7:cloud",
        temperature=0.5,
    )
    llm_with_tools = llm.bind_tools(tools)

    async def agent_node(state: AgentState):
        msgs = [SystemMessage(content=SYSTEM_PROMPT + "\n\n" + AGENTS_MD)] + state.messages
        return {"messages": [await llm_with_tools.ainvoke(msgs)]}

    def extractor_node(state: AgentState):
        """Pure-Python extraction — no LLM call, no token-limit risk."""
        search_blob = next(
            (
                _to_text(msg.content)
                for msg in state.messages
                if isinstance(msg, ToolMessage) and msg.name and "airbnb_search" in msg.name
            ),
            "",
        )
        detail_blobs = [
            _to_text(msg.content)
            for msg in state.messages
            if isinstance(msg, ToolMessage)
            and msg.name
            and "airbnb_listing_details" in msg.name
        ]
        return {"listings": _extract_listings(search_blob, detail_blobs)}

    def confirm_node(state: AgentState):
        listings = state.listings
        interrupt({
            "question": (
                f"Found {len(listings)} listing(s). "
                "Ready to generate the Excel sheet and send it to your email? (yes/no)"
            ),
            "listings": listings,
        })
        return {}

    builder = StateGraph(AgentState)
    builder.add_node("llm", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("extractor", extractor_node)
    builder.add_node("confirm", confirm_node)

    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", tools_condition)
    builder.add_conditional_edges("tools", _route_after_tools, {"extractor": "extractor", "llm": "llm"})
    builder.add_edge("extractor", "confirm")
    builder.add_edge("confirm", END)

    return builder.compile(checkpointer=checkpointer)


async def get_graph(session, checkpointer):
    """Build a per-request graph — session keeps the MCP subprocess alive for the call."""
    airbnb_tools = await load_mcp_tools(session)
    all_tools = [get_places_id] + airbnb_tools
    return build_graph(all_tools, checkpointer)
