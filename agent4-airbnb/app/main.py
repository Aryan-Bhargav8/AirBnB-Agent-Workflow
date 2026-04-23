import os
import uuid

os.environ.pop("SSL_CERT_FILE", None)

from fastapi import FastAPI, Request
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.types import Command

import app.lifespan as _lifespan
from app.lifespan import lifespan
from app.agent import get_graph
from app.pipeline import listings_to_csv, csv_to_xlsx, send_email

app = FastAPI(lifespan=lifespan)


class ChatRequest(BaseModel):
    messages: list[dict]           # [{"role": "user", "content": "..."}]
    thread_id: str | None = None   # present on resume turns
    confirmed: bool = False        # user confirmed → trigger pipeline
    email: str | None = None       # required when confirmed=True


class ChatResponse(BaseModel):
    response: str
    thread_id: str | None = None
    awaiting_confirmation: bool = False


def _extract_text(msg) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        return " ".join(
            block.get("text", "") for block in msg.content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(msg.content)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    # ── Resume turn: user confirmed, trigger pipeline ──────────────────────
    if req.thread_id and req.confirmed:
        config = {"configurable": {"thread_id": req.thread_id}}

        async with _lifespan.mcp_client.session("airbnb") as session:
            graph = await get_graph(session, _lifespan.checkpointer)
            result = await graph.ainvoke(Command(resume=True), config=config)

        listings = result.get("listings", [])
        to_email = req.email or ""

        if not listings:
            return ChatResponse(
                response="No listings were found to export. Please try your search again.",
                thread_id=req.thread_id,
            )

        try:
            csv_path = listings_to_csv(listings)
            xlsx_path = csv_to_xlsx(csv_path)
            send_email(xlsx_path, to_email, listings)
            return ChatResponse(
                response=f"Done! Excel sheet with {len(listings)} listing(s) sent to {to_email}.",
                thread_id=req.thread_id,
            )
        except Exception as exc:
            return ChatResponse(
                response=(
                    f"Found {len(listings)} listing(s) but email delivery failed: {exc}. "
                    "Please check your Gmail credentials and try again."
                ),
                thread_id=req.thread_id,
            )

    # ── First turn ─────────────────────────────────────────────────────────
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    lc_messages = [
        HumanMessage(content=msg["content"]) if msg["role"] == "user"
        else AIMessage(content=msg["content"])
        for msg in req.messages
        if msg["role"] in ("user", "assistant")
    ]

    async with _lifespan.mcp_client.session("airbnb") as session:
        graph = await get_graph(session, _lifespan.checkpointer)
        await graph.ainvoke({"messages": lc_messages}, config=config)
        state_snapshot = await graph.aget_state(config)

    if state_snapshot.next:
        interrupts = state_snapshot.tasks[0].interrupts if state_snapshot.tasks else []
        interrupt_val = interrupts[0].value if interrupts else {}
        question = interrupt_val.get(
            "question",
            "Listings collected. Shall I send the Excel sheet to your email? (yes/no)",
        )
        return ChatResponse(
            response=question,
            thread_id=thread_id,
            awaiting_confirmation=True,
        )

    messages = state_snapshot.values.get("messages", [])
    last_msg = messages[-1] if messages else None
    response_text = _extract_text(last_msg) if last_msg else "No response."

    return ChatResponse(response=response_text, thread_id=thread_id)


@app.get("/")
async def health():
    return {"health": "working"}
