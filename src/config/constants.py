"""Shared constants — agent ports, URLs, timeouts, and budget defaults.

Import from here instead of hardcoding values inline.
"""

# ── Agent network ─────────────────────────────────────────────────────────────

AGENT_HOST = "127.0.0.1"

FLIGHT_PORT      = 8001
ATTRACTIONS_PORT = 8002
HOTEL_PORT       = 8003
TRANSPORT_PORT   = 8004

FLIGHT_URL      = f"http://{AGENT_HOST}:{FLIGHT_PORT}"
ATTRACTIONS_URL = f"http://{AGENT_HOST}:{ATTRACTIONS_PORT}"
HOTEL_URL       = f"http://{AGENT_HOST}:{HOTEL_PORT}"
TRANSPORT_URL   = f"http://{AGENT_HOST}:{TRANSPORT_PORT}"

# All agent URLs — used by AgentRegistry for dynamic peer discovery
ALL_AGENT_URLS: list[str] = [FLIGHT_URL, ATTRACTIONS_URL, HOTEL_URL, TRANSPORT_URL]

# ── HTTP timeouts (seconds) ───────────────────────────────────────────────────

SEND_TIMEOUT   = 600.0   # agent tasks can take up to 10 min
CARD_TIMEOUT   =  10.0   # AgentCard discovery
HEALTH_TIMEOUT =   5.0   # health-check poll

# ── Agent startup ─────────────────────────────────────────────────────────────

STARTUP_TIMEOUT = 120.0  # seconds to wait for all servers to become healthy

# ── Budget split defaults (fractions of total budget per agent) ───────────────

DEFAULT_BUDGET_SPLIT: dict[str, float] = {
    "flights":     0.40,
    "attractions": 0.20,
    "hotel":       0.30,
    "transport":   0.10,
}

# ── Budget and retry tuning ───────────────────────────────────────────────────

BUDGET_HOTEL_BUFFER = 1.20   # search hotels up to 20 % above per-night cap
RETRY_CAP_FACTOR    = 0.85   # tighten flight cap by 15 % on each budget retry

# ── Rate-limit retry (CrewAI agents) ─────────────────────────────────────────

RATE_LIMIT_MAX_ATTEMPTS  = 6    # total attempts (1 initial + 5 retries)
RATE_LIMIT_MAX_RETRIES   = 5    # retries after first failure (ATTEMPTS - 1)
RATE_LIMIT_BASE_WAIT     = 30   # seconds for first retry when no header found
RATE_LIMIT_BACKOFF_BASE  = 2    # exponential base: wait = BASE_WAIT * BACKOFF_BASE ** attempt
