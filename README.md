# Multi-Agent Travel Planning System

A travel planning application built on the **Google A2A (Agent-to-Agent) protocol**. Four independent agents — each a self-contained HTTP server — run **in parallel**. The coordinator uses an LLM to decide which agents to invoke based on the user's query, then fires them all simultaneously. Agents enrich their own output by calling peer agents directly via A2A — the coordinator never passes results between them.

## Architecture

```
Streamlit UI  (user types natural-language query)
      │
      ▼
 Coordinator  ── Phase 1: LLM reads user query → decides which agents to invoke
      │         Phase 2: fires all needed agents in PARALLEL via POST /send_message
      │
      ├──────────────────────────────────────────────────────────┐
      │                          │                │              │
      ▼                          ▼                ▼              ▼
Flight Agent :8001        Attractions      Hotel Agent     Transport Agent
(LangGraph)               Agent :8002      :8003           :8004
RAG: travel_policies      (LangGraph)      (CrewAI)        (CrewAI)
MCP: search_flights       RAG: destinations RAG: hotel_details RAG: destinations
                          RAG: preferences  MCP: search_hotels MCP: search_transit
                          RAG: itineraries
      │                          │                │              │
      │   ◄── A2A peer call ─────┘                │              │
      │   (query_type=date_confirmation)           │              │
      │                          │                │              │
      │                          └── A2A ────────►│              │
      │                          (query_type=      │              │
      │                           cluster_areas)   │              │
      │                                            │              │
      └────────────────── A2A ───────────────────►│◄─── A2A ────┘
        (query_type=date_confirmation)    (query_type=hotel_location)
                                          (query_type=cluster_areas)

Vector DB: FAISS  (built once from data/ JSON files, cached on disk)
LLM:       Gemini (configurable per agent via .env)
```

### How the coordinator decides which agents to call

The coordinator does **not** use hardcoded conditions. It passes the user's original query to an LLM (using `prompts/coordinator_routing.md`) which reasons about intent and returns a JSON routing decision:

```json
{"flights": true, "attractions": true, "hotel": true, "transport": false, "reasoning": "..."}
```

Pre-booked items are always forced to `false` regardless of LLM output. If the LLM call fails, it falls back to condition-based logic.

### How agents communicate with each other (true A2A)

Agents do **not** receive peer results from the coordinator. Instead each agent asks its peers live, targeted questions via `POST /send_message` with a `query_type` in the data:

| Calling agent | Asks | Query type | Gets back |
|---|---|---|---|
| Attractions | Flight Agent | `date_confirmation` | `confirmed_dates` |
| Hotel | Attractions Agent | `cluster_areas` | `clusters` list |
| Transport | Flight Agent | `date_confirmation` | `confirmed_dates` |
| Transport | Attractions Agent | `cluster_areas` | `clusters` list |
| Transport | Hotel Agent | `hotel_location` | `hotel_location`, `hotel_name` |

Each agent stores its completed result in a session-scoped dict (`_session_results[session_id]`) so peer queries are answered instantly once the main task is done. If a peer is unavailable or was pre-booked (skipped), the calling agent falls back gracefully to the original request data.

### What the coordinator passes to each agent

The coordinator passes **only the original request fields** to every agent — no upstream results:

| Field | All agents receive |
|---|---|
| `origin`, `destination` | trip endpoints |
| `start_date`, `end_date` | travel dates |
| `interests` | traveler preferences |
| `budget_cap` | this agent's allocated budget |
| `session_id` | shared key for peer queries |

## Requirements

- Python 3.11–3.13
- A Google API key with the **Generative Language API** enabled

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd Multi_agent_travel_planning

python3.13 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
# Runtime only
pip install -e .

# Runtime + dev tools (pytest, ruff)
pip install -e ".[dev]"
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your API key:

```env
LLM_PROVIDER=gemini
GOOGLE_API_KEY=AIzaSy...
```

#### Getting a Google API key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and create a key in a new project.
2. In [Google Cloud Console](https://console.cloud.google.com/apis/library), enable **Generative Language API**.
3. Under **APIs & Services → Credentials**, edit the key and set restrictions to **"Don't restrict key"**.
4. Enable billing on the project (required for `generateContent` — very low cost for dev use).

### 4. Verify the setup

```bash
.venv/bin/python test_gemini.py
```

Expected output:

```
[1] Chat:
    PASSED  gemini-2.0-flash-lite
    Response: Hello! How can I help you today?

[2] Embeddings — model: gemini-embedding-2-preview
    Vector length: 3072  —  PASSED
```

### 5. Run the app

```bash
.venv/bin/streamlit run app.py
```

On first run the FAISS indexes are built from the `data/` JSON files and cached to `data/faiss/`. The four agent servers are started as subprocesses on ports 8001–8004 and are reused across Streamlit reloads.

## Project Structure

```
├── app.py                      # Streamlit entry point
├── prompts/                    # Prompt files (one .md per agent/role)
│   ├── coordinator_routing.md  # LLM agent-selection prompt
│   ├── flight_agent.md
│   ├── attractions_agent.md
│   ├── hotel_agent.md
│   └── transport_agent.md
├── data/                       # Knowledge base JSON files + FAISS cache
│   ├── destinations.json
│   ├── flights.json
│   ├── hotels.json
│   ├── itineraries.json
│   ├── policies.json
│   ├── preferences.json
│   ├── transit.json
│   └── faiss/                  # Auto-generated FAISS indexes (git-ignored)
├── src/
│   ├── agents/                 # Self-contained A2A agent servers
│   │   ├── flight.py           # LangGraph — :8001
│   │   ├── attractions.py      # LangGraph — :8002
│   │   ├── hotel.py            # CrewAI    — :8003
│   │   └── transport.py        # CrewAI    — :8004
│   ├── a2a/                    # Google A2A protocol layer
│   │   ├── client.py           # A2AClient — typed HTTP client
│   │   ├── server.py           # create_agent_app() factory
│   │   ├── models.py           # Pydantic v2 A2A message/task models
│   │   └── launch.py           # AgentServerManager (uvicorn subprocesses)
│   ├── config/
│   │   ├── settings.py         # Pydantic-settings (.env loader)
│   │   └── constants.py        # Ports, URLs, timeouts, budget defaults
│   ├── coordinator/
│   │   ├── coordinator.py      # Main orchestration loop
│   │   ├── models.py           # TravelRequest, AgentContext
│   │   └── session.py          # TravelSession (budget accounting)
│   ├── llm/                    # LLM + embeddings factory (Gemini / OpenAI / Anthropic)
│   ├── rag/                    # FAISS bootstrap and retriever
│   ├── mcp/                    # MCP tool server (search_flights, search_hotels, search_transit)
│   ├── tools/                  # Tool implementations called by agents
│   ├── ui/                     # Streamlit UI (chat stages, components, session state)
│   ├── prompts.py              # Markdown prompt loader
│   └── utils/                  # Shared helpers (parse_agent_json, budget split, etc.)
└── tests/                      # Unit tests
```

## Development

```bash
# Run tests
.venv/bin/pytest tests/ -v

# Lint and format
.venv/bin/ruff check src/
.venv/bin/ruff format src/

# Run MCP server standalone
.venv/bin/python -m src.mcp.server
```

## Configuration

All settings live in `.env`. Key options:

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` \| `anthropic` \| `openai` |
| `GOOGLE_API_KEY` | — | Required for Gemini provider |
| `COORDINATOR_MODEL` | `gemini-3.1-flash-lite` | Model used by the coordinator |
| `FLIGHT_AGENT_MODEL` | `gemini-3.1-flash-lite` | Model for the Flight Agent |
| `ATTRACTIONS_AGENT_MODEL` | `gemini-3.1-flash-lite` | Model for the Attractions Agent |
| `HOTEL_AGENT_MODEL` | `gemini-3.1-flash-lite` | Model for the Hotel Agent |
| `TRANSPORT_AGENT_MODEL` | `gemini-3.1-flash-lite` | Model for the Transport Agent |
| `EMBEDDING_MODEL` | `gemini-embedding-2-preview` | Embedding model for FAISS RAG |
| `DEFAULT_TOTAL_BUDGET` | `3000.0` | Default trip budget in USD |
| `RAG_TOP_K` | `3` | Chunks retrieved per RAG query |
| `AGENT_MAX_RETRIES` | `3` | Max flight budget retry attempts |
| `RPM_THROTTLE_SECONDS` | `4.0` | Throttle delay between LLM calls (free-tier safety) |

## A2A Protocol

Each agent exposes three endpoints following the [Google A2A spec](https://google.github.io/A2A/):

| Endpoint | Method | Purpose |
|---|---|---|
| `/.well-known/agent.json` | GET | AgentCard discovery |
| `/health` | GET | Health check |
| `/send_message` | POST | JSON-RPC 2.0 task submission |

The coordinator discovers all agents at startup via `GET /.well-known/agent.json` and `GET /health`, then fires all needed agents **in parallel** via `POST /send_message`. Agents call each other directly using the same endpoint with a `query_type` field. Results are returned as `DataPart` objects inside an A2A `Task` artifact.
