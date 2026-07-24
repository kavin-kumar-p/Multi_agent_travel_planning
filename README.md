# Multi-Agent Travel Planning System

A travel planning application built with a multi-agent architecture. Separate agents handle flights, hotels, transport, and attractions — coordinated by a central agent that manages budget and assembles the final itinerary.

## Architecture

```
Coordinator (Google ADK)
├── Flight Agent      (LangGraph) — RAG: travel_policies  + MCP: search_flights
├── Attractions Agent (LangGraph) — RAG: destinations, preferences, itineraries
├── Hotel Agent       (CrewAI)    — RAG: hotel_details    + MCP: search_hotels
└── Transport Agent   (CrewAI)    — RAG: destinations     + MCP: search_transit

Vector DB: FAISS (built once from data/ JSON files)
LLM:       Gemini (configurable via .env)
```

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

Open `.env` and fill in your API key:

```env
LLM_PROVIDER=gemini
GOOGLE_API_KEY=AIzaSy...         # from console.cloud.google.com/apis/credentials
```

#### Getting a Google API key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and create a key in a **new project**
2. In [Google Cloud Console](https://console.cloud.google.com/apis/library), enable **Generative Language API**
3. Under **APIs & Services → Credentials**, edit the key and set API restrictions to **"Don't restrict key"**
4. Enable billing on the project (required for `generateContent` — very low cost for dev use)

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

### 5. Run the system

```bash
.venv/bin/python main.py
```

This will:
1. Bootstrap FAISS indexes from `data/` JSON files (first run only)
2. Run the coordinator with a sample request (Paris, 5 nights, $3000 budget)
3. Print the full itinerary as JSON

## Project Structure

```
├── data/               # Sample JSON data (policies, destinations, hotels, etc.)
├── prompts/            # Agent prompt files (.md, one per agent)
├── src/
│   ├── config/         # Settings loaded from .env via pydantic-settings
│   ├── llm/            # LLM + embeddings factory (Gemini / Anthropic / OpenAI)
│   ├── rag/            # FAISS bootstrap and retriever
│   ├── mcp/            # MCP tool implementations + FastMCP server
│   ├── agents/         # flight, attractions, hotel, transport
│   ├── coordinator/    # Budget split, agent dispatch, session state
│   ├── prompts.py      # Markdown prompt loader
│   └── utils.py        # Shared helpers
├── tests/              # Unit tests
├── main.py             # Entry point
├── test_gemini.py      # API key + model sanity check
└── pyproject.toml      # Dependencies and tooling config
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

All settings are in `.env`. Key options:

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` \| `anthropic` \| `openai` |
| `GOOGLE_API_KEY` | — | Required for Gemini provider |
| `COORDINATOR_MODEL` | `gemini-2.0-flash` | Model for the coordinator agent |
| `FLIGHT_AGENT_MODEL` | `gemini-2.0-flash-lite` | Model for all sub-agents |
| `EMBEDDING_MODEL` | `gemini-embedding-2-preview` | Embedding model for RAG |
| `DEFAULT_TOTAL_BUDGET` | `3000.0` | Default trip budget in USD |
| `RAG_TOP_K` | `3` | Number of chunks retrieved per RAG query |
