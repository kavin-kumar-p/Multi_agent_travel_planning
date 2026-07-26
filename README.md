# Multi-Agent Travel Planning System

A travel planning application built on the **Google A2A (Agent-to-Agent) protocol**. Four independent agents — each a self-contained HTTP server — run **in parallel**. The coordinator uses **Google ADK** to decide which agents to invoke based on the user's query, then fires them all simultaneously. Agents enrich their own output by calling peer agents directly via A2A — the coordinator never passes results between them.

## Architecture

```
Streamlit UI  (user types natural-language query)
      │
      ▼
 Coordinator  ── Google ADK LlmAgent reads user query → decides which agents to invoke
      │         Fires all needed agents in PARALLEL via POST /send_message (A2A)
      │
      ├─────────────────────────────────────────────────────────┐
      │                          │               │              │
      ▼                          ▼               ▼              ▼
Flight Agent :8001        Attractions      Hotel Agent     Transport Agent
LangGraph                 Agent :8002      :8003           :8004
  RAG: travel_policies    LangGraph        CrewAI          CrewAI
  MCP: search_flights        RAG: destinations  RAG: hotel_details  RAG: destinations
                             RAG: preferences   MCP: search_hotels  MCP: search_transit
                             RAG: itineraries
      │                          │               │              │
      │   ◄── A2A peer call ─────┘               │              │
      │   (call_peer_agent tool)                  │              │
      │                          │               │              │
      │                          └── A2A ───────►│              │
      │                          (call_peer_agent │              │
      │                               tool)       │              │
      │                                           │              │
      └─────────────── A2A ──────────────────────►│◄─── A2A ────┘
              (call_peer_agent)            (call_peer_agent x2)

Vector DB: FAISS  (built once from data/ JSON files, cached on disk)
LLM:       Gemini (configurable per agent via .env)
```

## Framework Assignment

| Component | Framework | Role |
|---|---|---|
| Coordinator | **Google ADK** `LlmAgent` + `Runner` | Routes request to correct agents |
| Flight Agent | **LangGraph** `create_react_agent` | Selects optimal flights, RAG + MCP |
| Attractions Agent | **LangGraph** `create_react_agent` | Plans day-by-day itinerary, RAG |
| Hotel Agent | **CrewAI** `Crew` + `Agent` | Selects optimal hotel, RAG + MCP |
| Transport Agent | **CrewAI** `Crew` + `Agent` | Plans ground transport, RAG + MCP |
| Vector DB | **FAISS** (LangChain Community) | RAG index for all knowledge collections |
| Inventory tools | **MCP-style** JSON tools | search_flights, search_hotels, search_transit |

## How the Coordinator Decides Which Agents to Call

The coordinator uses a **Google ADK `LlmAgent`** — not hardcoded conditions. It creates an ADK `Runner` with an `InMemorySessionService`, sends the user's original query to the agent, and streams the events until `is_final_response()`. The agent returns a JSON routing decision:

```json
{"flights": true, "attractions": true, "hotel": true, "transport": false, "reasoning": "..."}
```

Pre-booked items are always forced to `false`. If the ADK call fails, it falls back to condition-based logic.

## How Agents Communicate with Each Other (True A2A)

Agents do **not** receive peer results from the coordinator. Instead, each agent has A2A peer-call tools registered in its own ReAct loop or CrewAI crew. The LLM decides autonomously when to call a peer:

| Calling agent | Tool name | Peer resolved at runtime | Asks | Gets back |
|---|---|---|---|---|
| Attractions | `call_peer_agent` | Flight Agent | "What are the confirmed travel dates?" | `confirmed_dates` |
| Hotel | `call_peer_agent` | Attractions Agent | "Which areas are the attraction clusters?" | `clusters` list |
| Transport | `call_peer_agent` | Flight Agent | "What are the confirmed travel dates?" | `confirmed_dates` |
| Transport | `call_peer_agent` | Attractions Agent | "Which areas are the attraction clusters?" | `clusters` list |
| Transport | `call_peer_agent` | Hotel Agent | "Where is the hotel located?" | `hotel_location` |

Each agent has exactly one generic `call_peer_agent(agent_name, question)` tool. On first use the agent calls `GET /.well-known/agent.json` on all peer URLs to discover their names and descriptions (`AgentRegistry` in `src/a2a/registry.py`). The discovered names and descriptions are injected into the system prompt, and the LLM autonomously decides which peer to call and when — no developer hardcodes the dependency.

Each agent is stateless — there is no in-process result cache. When a peer agent calls with a question, the receiving agent runs its full pipeline fresh (RAG search + LLM reasoning) to answer. This is the correct production pattern: stateless, independently scalable services where shared state lives in an external store (Redis, database) if needed, not in process memory.

## What the Coordinator Passes to Each Agent

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
│   ├── coordinator_routing.md  # ADK LlmAgent routing prompt
│   ├── flight_agent.md         # system + autonomous_decision_making + user_template
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
│   │   ├── flight.py           # LangGraph create_react_agent — :8001
│   │   ├── attractions.py      # LangGraph create_react_agent — :8002
│   │   ├── hotel.py            # CrewAI Crew + Agent          — :8003
│   │   └── transport.py        # CrewAI Crew + Agent          — :8004
│   ├── a2a/                    # Google A2A protocol layer
│   │   ├── client.py           # A2AClient — typed HTTP client
│   │   ├── server.py           # create_agent_app() factory
│   │   ├── models.py           # Pydantic v2 A2A message/task models
│   │   ├── registry.py         # AgentRegistry — discovers peers via AgentCard, caches name→URL
│   │   └── launch.py           # AgentServerManager (uvicorn subprocesses)
│   ├── config/
│   │   ├── settings.py         # Pydantic-settings (.env loader)
│   │   └── constants.py        # Ports, URLs, timeouts, budget defaults
│   ├── coordinator/
│   │   ├── coordinator.py      # Google ADK routing + A2A orchestration
│   │   ├── models.py           # TravelRequest, AgentContext
│   │   └── session.py          # TravelSession (budget accounting)
│   ├── llm/                    # LLM + embeddings factory (Gemini / OpenAI / Anthropic)
│   │   └── google_genai_model.py  # GoogleGenAIChatModel (bind_tools + react_loop)
│   ├── rag/                    # FAISS bootstrap and retriever
│   ├── mcp/                    # MCP tool server (search_flights, search_hotels, search_transit)
│   ├── tools/
│   │   ├── agent_tools.py      # Tool factories — LangGraph (StructuredTool) + CrewAI (BaseTool)
│   │   ├── flights.py          # search_flights() — MCP inventory
│   │   ├── hotels.py           # search_hotels()  — MCP inventory
│   │   └── transit.py          # search_transit() — MCP inventory
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
| `COORDINATOR_MODEL` | `gemini-3.1-flash-lite` | ADK LlmAgent model for routing |
| `FLIGHT_AGENT_MODEL` | `gemini-3.1-flash-lite` | LangGraph agent model |
| `ATTRACTIONS_AGENT_MODEL` | `gemini-3.1-flash-lite` | LangGraph agent model |
| `HOTEL_AGENT_MODEL` | `gemini-3.1-flash-lite` | CrewAI LLM model |
| `TRANSPORT_AGENT_MODEL` | `gemini-3.1-flash-lite` | CrewAI LLM model |
| `EMBEDDING_MODEL` | `gemini-embedding-2-preview` | Embedding model for FAISS RAG |
| `DEFAULT_TOTAL_BUDGET` | `3000.0` | Default trip budget in USD |
| `RAG_TOP_K` | `3` | Chunks retrieved per RAG query |
| `AGENT_MAX_RETRIES` | `3` | Max flight budget retry attempts |
| `RPM_THROTTLE_SECONDS` | `4.0` | Throttle delay between LLM calls (free-tier safety) |

---

## A2A Protocol

Each agent exposes three endpoints following the [Google A2A spec](https://google.github.io/A2A/):

| Endpoint | Method | Purpose |
|---|---|---|
| `/.well-known/agent.json` | GET | AgentCard discovery |
| `/health` | GET | Health check |
| `/send_message` | POST | JSON-RPC 2.0 task submission |

The coordinator discovers all agents at startup via `GET /.well-known/agent.json` and `GET /health`, then fires all needed agents **in parallel** via `POST /send_message`. Agents call each other using the same endpoint — each agent carries a single generic `call_peer_agent(agent_name, question)` tool registered in its LangGraph or CrewAI toolset. At runtime `AgentRegistry` (`src/a2a/registry.py`) resolves `agent_name` to the correct peer URL by fetching AgentCards from all candidate peers and matching by name.
