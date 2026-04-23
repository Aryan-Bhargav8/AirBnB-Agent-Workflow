# agent4-airbnb

A Dockerized LangGraph agent that searches Airbnb listings, pauses for human confirmation, then generates an Excel sheet and emails it to the user.

## What it does

1. User sends a natural-language request with location, dates, and email
2. Agent resolves the location to a Google Maps Place ID, searches Airbnb via MCP, and fetches full details for every listing
3. Agent pauses — the system asks the user to confirm before proceeding
4. On confirmation: listings → CSV → XLSX → sent via Gmail SMTP

## Architecture

```
POST /chat
     │
     ▼
  llm node  ──────────────────────────────────────────────────────────┐
     │                                                                │
     │ (tool calls)                                                   │
     ▼                                                                │
 tools node                                                           │
     │                                                                │
     ├─ last tool = airbnb_search       ──► llm node  (fetch details) ┘
     │
     └─ last tool = airbnb_listing_details ──► extractor node
                                                     │
                                                     ▼
                                               confirm node  ◄── LangGraph interrupt()
                                                     │
                                                     ▼
                                                   END

Resume turn (confirmed=true):
  Command(resume=True) → pipeline → CSV → XLSX → Gmail
```

### Nodes

| Node | Role |
|---|---|
| `llm` | Calls the LLM with tools bound; handles all tool-calling turns |
| `tools` | `ToolNode` — executes `get_places_id`, `airbnb_search`, `airbnb_listing_details` |
| `extractor` | Pure-Python extraction of structured listing dicts from tool message blobs — no LLM call |
| `confirm` | Fires `interrupt()` — graph pauses here, state saved in `MemorySaver` |

### Routing

`_route_after_tools` inspects the last `ToolMessage` name to decide next node:
- `airbnb_listing_details` → `extractor` (all details fetched, ready to extract)
- `airbnb_search` → `llm` (agent needs to fetch per-listing details next)

## Tech Stack

- **LangGraph** `StateGraph` with `MemorySaver` checkpointer (in-memory, per-thread)
- **LLM** — Anthropic-compatible endpoint via Ollama at `host.docker.internal:11434` (local model, no API cost)
- **MCP** — `@openbnb/mcp-server-airbnb` via `npx` stdio transport, wrapped with `langchain-mcp-adapters`
- **Google Places API** — resolves location strings to Place IDs before Airbnb search
- **FastAPI** — `POST /chat`, `GET /` health check
- **Pipeline** — `pandas` + `openpyxl` for CSV→XLSX, `smtplib` SMTP SSL (port 465) for delivery
- **Pydantic-settings** with `SecretStr` for all credentials — never logged or in image layers
- **Docker** — `python:3.12-slim-bookworm`, non-root `appuser`, Node.js bundled for MCP

## API

### `POST /chat`

**First turn** — start a new search:
```json
{
  "messages": [{"role": "user", "content": "Find listings in Bali from Dec 1-7, send to me@email.com"}],
  "thread_id": null
}
```

**Response** (interrupted, awaiting confirmation):
```json
{
  "response": "Found 24 listings. Ready to generate the Excel sheet and send it to your email? (yes/no)",
  "thread_id": "abc-123",
  "awaiting_confirmation": true
}
```

**Resume turn** — user confirms:
```json
{
  "messages": [],
  "thread_id": "abc-123",
  "confirmed": true,
  "email": "me@email.com"
}
```

### `GET /`
Health check — returns `{"health": "working"}`.

## Running Locally

```bash
# Build
docker build -t agent4-airbnb ./agent4-airbnb

# Run
docker run --env-file agent4-airbnb/.env \
  -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  agent4-airbnb

# Health check
curl http://localhost:8000/

# First turn
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Listings in Tokyo, Dec 10-15, send to me@email.com"}]}'
```

> **Windows / Git Bash**: if `--add-host` is not needed (Ollama on same machine), Docker Desktop handles `host.docker.internal` automatically.

## Environment Variables

Copy `.env.example` to `.env` and fill in values. Never commit `.env`.

```
PLACES_API_KEY=          # Google Cloud → APIs & Services → Places API (New)
LANGSMITH_API_KEY=       # optional — LangSmith tracing
LANGSMITH_PROJECT=       # optional
GMAIL_ADDRESS=           # sender Gmail address
GMAIL_APP_PASSWORD=      # Google Account → Security → App Passwords (not your login password)
```

> **Gmail setup**: enable 2FA on the account, then generate an App Password specifically for this app. Use that 16-character password here — your regular password will not work.

## Design Decisions & Trade-offs

### Pure-Python `extractor` node, not an LLM extraction call
Airbnb search returns large JSON blobs. Asking the LLM to extract structured fields from them risks hitting context limits and costs tokens on every run. The `_extract_listings()` helper parses predictable JSON paths directly. Trade-off: fragile to Airbnb API response shape changes, but zero token cost and no risk of hallucinated data.

### `MemorySaver` (in-memory) not a persistent checkpointer
State is stored in process memory keyed by `thread_id`. Restarting the container loses all in-progress sessions. Trade-off: simple, no database dependency — acceptable since the full flow completes in one session.

### MCP via `npx` stdio transport
`@openbnb/mcp-server-airbnb` runs as a child process; `langchain-mcp-adapters` wraps it as LangChain tools. Trade-off: adds Node.js to the image (~50 MB) and spawns a subprocess per session, but keeps the Airbnb tool implementation decoupled and language-agnostic.

### Routing by tool message name, not LLM decision
`_route_after_tools` reads `msg.name` to decide whether to go to `extractor` or loop back to `llm`. This is deterministic and fast. Trade-off: brittle if the MCP server renames its tools, but avoids an extra LLM call just to decide routing.

### LLM at `host.docker.internal:11434` (Ollama)
The LLM is served locally via Ollama, not a cloud API. Trade-off: zero per-token cost and no external API dependency, but requires Ollama running on the host with the model already pulled. The `api_key="ollama"` placeholder satisfies the SDK's required field — it is not a real secret.

### `requests` (sync) inside an async tool
`get_places_id` uses `requests.post` inside an `async def` tool. This technically blocks the event loop for the duration of the HTTP call. Trade-off: simple and correct for a single-user Docker container; for production concurrency, replace with `httpx` and `await`.

### Gmail SMTP SSL port 465, not STARTTLS port 587
Port 465 (direct SSL) is simpler — no `starttls()` handshake step. Trade-off: slightly less universal than 587, but well-supported by Gmail and avoids a common footgun where developers forget to call `starttls()` and send credentials in plaintext.

### `confirm` node uses `interrupt()`, not a second endpoint
The human-in-the-loop pause is implemented as a LangGraph `interrupt()` inside the graph rather than a separate `/confirm` endpoint. The graph state (listings) is preserved in the checkpointer and resumed with `Command(resume=True)`. Trade-off: the client must track `thread_id` and send a resume turn, but the pipeline trigger logic stays inside the graph where it belongs.
