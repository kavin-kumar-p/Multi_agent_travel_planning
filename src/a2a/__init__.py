"""A2A (Agent-to-Agent) protocol implementation — Google A2A spec.

Each specialist agent runs as an independent FastAPI HTTP service.
The coordinator discovers agents via AgentCard, health-checks them,
then calls POST /send_message. All communication uses JSON-RPC 2.0.

Ports:
  8001 — Flight Agent      (LangGraph)
  8002 — Attractions Agent (LangGraph)
  8003 — Hotel Agent       (CrewAI)
  8004 — Transport Agent   (CrewAI)
Coordinator: Google ADK (LlmAgent routing) + A2A orchestration

Protocol endpoints on every agent:
  GET  /.well-known/agent.json  — AgentCard discovery
  GET  /health                  — health check
  POST /send_message            — submit a task (JSON-RPC 2.0)
"""
