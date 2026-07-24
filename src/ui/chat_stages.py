"""Chat stage state machine — mirrors the terminal flow from main.py."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import streamlit as st

from src.utils.extractor import extract_fields, follow_up, merge_info, missing_fields

# Budget constants (same as budget_ui.py)
_ACTIVE_WEIGHTS = {"flights": 40, "hotel": 30, "attractions": 20, "transport": 10}
_ALL_ROWS = [
    ("flights",     "Flights"),
    ("hotel",       "Hotel"),
    ("attractions", "Attractions"),
    ("transport",   "Transport"),
]
_LABELS = {"flights": "Flights", "hotel": "Hotel", "transport": "Transport"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _render_history() -> None:
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _assistant(text: str) -> None:
    st.session_state.chat_history.append({"role": "assistant", "content": text})


def _user(text: str) -> None:
    st.session_state.chat_history.append({"role": "user", "content": text})


def _go_to_budget_setup() -> None:
    confirmed = st.session_state.confirmed_booked
    booked_keys = [k for k in ("flights", "hotel", "transport") if confirmed.get(k)]
    st.session_state.budget_setup_step = "deduct_q" if booked_keys else "split"
    st.session_state.stage = "budget_setup"


def _after_confirm_or_input() -> None:
    """Decide next stage after initial extraction or after all confirmations."""
    info = st.session_state.info
    has_budget = bool(info.get("total_budget"))
    if has_budget:
        st.session_state.total_budget = float(info["total_budget"])
        _go_to_budget_setup()
    else:
        st.session_state.stage = "followup"
    st.rerun()


def _start_planning_thread(rag_stores: dict) -> None:
    from src.coordinator.coordinator import TravelRequest
    from src.coordinator.coordinator import run as coordinator_run

    info      = st.session_state.info
    confirmed = st.session_state.confirmed_booked
    split     = st.session_state.split_fractions or {}
    p_budget  = float(st.session_state.planning_budget or st.session_state.total_budget or 0)

    # Original query from the chat box — first user message in history
    user_query = next(
        (m["content"] for m in st.session_state.chat_history if m["role"] == "user"),
        "",
    )

    # Pass user-configured budget fractions; coordinator falls back to defaults if None
    budget_split = {k: split.get(k, 0.0) for k in ("flights", "attractions", "hotel", "transport")} if split else None

    request = TravelRequest(
        origin=info.get("origin", ""),
        destination=info.get("destination", ""),
        start_date=info.get("start_date", ""),
        end_date=info.get("end_date", ""),
        total_budget=p_budget,
        interests=info.get("interests") or [],
        confirmed_flight={"_pre_booked": True}   if confirmed.get("flights")   else None,
        confirmed_hotel={"_pre_booked": True}    if confirmed.get("hotel")     else None,
        confirmed_transport={"_pre_booked": True} if confirmed.get("transport") else None,
        user_query=user_query,
        budget_split=budget_split,
    )

    trace_sink  = st.session_state.trace_sink
    result_box  = st.session_state.result_box

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                coordinator_run(request, rag_stores, trace_sink=trace_sink)
            )
            result_box["result"] = result
        except Exception as exc:
            result_box["error"] = str(exc)
            trace_sink.append({"agent": "coordinator", "status": "error", "message": str(exc), "ts": ""})
        finally:
            # Cancel any lingering tasks (e.g. aiohttp connector cleanup from google.genai)
            # to prevent "Task was destroyed but it is pending!" warnings.
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    st.session_state.planning_thread = t


# ── Stage handlers ────────────────────────────────────────────────────────────

def _stage_input() -> None:
    _render_history()

    if not st.session_state.chat_history:
        with st.chat_message("assistant"):
            st.markdown(
                "**Welcome to Travel Planner AI!**\n\n"
                "Tell me about your trip and I'll create a complete itinerary.\n\n"
                "*Example: \"Trip from London to Bali in March, \\$3500 budget, love surfing and local food\"*"
            )

    if user_input := st.chat_input("Tell me about your trip…"):
        _user(user_input)
        with st.spinner("Extracting trip details…"):
            info = extract_fields(user_input)
        st.session_state.info = info

        booked = {
            "flights":   bool(info.get("flight_booked")),
            "hotel":     bool(info.get("hotel_booked")),
            "transport": bool(info.get("transport_booked")),
        }
        st.session_state.booked = booked
        st.session_state.confirmed_booked = dict(booked)
        st.session_state.confirm_items = [k for k in ("flights", "hotel", "transport") if booked.get(k)]
        st.session_state.confirm_idx   = 0

        if st.session_state.confirm_items:
            st.session_state.stage = "confirm_bookings"
            st.rerun()
        else:
            _after_confirm_or_input()


def _stage_confirm_bookings() -> None:
    _render_history()

    idx   = st.session_state.confirm_idx
    items = st.session_state.confirm_items

    if idx >= len(items):
        _after_confirm_or_input()
        return

    key  = items[idx]
    info = st.session_state.info
    origin = info.get("origin", "your origin")
    dest   = info.get("destination", "your destination")
    start  = info.get("start_date", "?")
    end    = info.get("end_date", "?")

    questions = {
        "flights": (
            f"You mentioned flights are already booked.\n\n"
            f"Quick check — did you book **BOTH** legs?\n"
            f"- Outbound: **{origin} → {dest}**\n"
            f"- Return: **{dest} → {origin}**"
        ),
        "hotel": (
            f"You mentioned the hotel is already booked.\n\n"
            f"Does your booking cover **ALL nights** ({start} → {end})?"
        ),
        "transport": (
            "You mentioned local transport is already arranged.\n\n"
            "Does that cover **BOTH** airport transfers AND daily local transport?"
        ),
    }
    question = questions[key]

    with st.chat_message("assistant"):
        st.markdown(question)

    col1, col2, _ = st.columns([1, 1.5, 2])
    with col1:
        if st.button("Yes — fully covered", key=f"yes_{idx}_{key}"):
            _assistant(question)
            _user("Yes, fully covered.")
            st.session_state.confirmed_booked[key] = True
            st.session_state.confirm_idx += 1
            st.rerun()
    with col2:
        if st.button("No — only partial", key=f"no_{idx}_{key}"):
            _assistant(question)
            _user("No, only partial — please plan the rest.")
            st.session_state.confirmed_booked[key] = False
            st.session_state.confirm_idx += 1
            st.rerun()


def _stage_budget_setup() -> None:
    _render_history()
    step = st.session_state.budget_setup_step

    confirmed  = st.session_state.confirmed_booked
    booked_keys = [k for k in ("flights", "hotel", "transport") if confirmed.get(k)]
    total       = st.session_state.total_budget or 0.0

    # ── Sub-step: deduct_q ────────────────────────────────────────────────────
    if step == "deduct_q":
        booked_labels = " + ".join(_LABELS[k] for k in booked_keys)
        question = (
            f"You mentioned **{booked_labels}** are already booked.\n\n"
            f"Is your **${total:,.0f}** budget the **TOTAL** trip cost "
            f"(including what you already paid), or is it what you have **LEFT** to spend?"
        )
        with st.chat_message("assistant"):
            st.markdown(question)

        col1, col2, _ = st.columns([1.5, 1.5, 1])
        with col1:
            if st.button("Total — deduct pre-booked", key="budget_total"):
                _assistant(question)
                _user("Total budget — please deduct pre-booked costs.")
                st.session_state.budget_setup_step = "deduct_amounts"
                st.rerun()
        with col2:
            if st.button("Remaining — what I have left", key="budget_remaining"):
                _assistant(question)
                _user("Remaining budget after pre-booked costs.")
                st.session_state.planning_budget = total
                st.session_state.budget_setup_step = "split"
                st.rerun()

    # ── Sub-step: deduct_amounts ──────────────────────────────────────────────
    elif step == "deduct_amounts":
        question = f"How much did you already pay for each pre-booked item? (in USD)"
        with st.chat_message("assistant"):
            st.markdown(question)

        if st.session_state.get("deduct_error"):
            st.error(st.session_state.deduct_error)

        with st.form("deduct_amounts_form"):
            amounts = {}
            for key in booked_keys:
                amounts[key] = st.number_input(
                    f"{_LABELS[key]} cost ($)",
                    min_value=0.0, value=0.0, step=10.0,
                    key=f"paid_{key}",
                )
            submitted = st.form_submit_button("Confirm amounts")
            if submitted:
                total_paid = sum(amounts.values())
                p_budget   = max(0.0, total - total_paid)
                details    = ", ".join(f"{_LABELS[k]}: ${amounts[k]:,.0f}" for k in booked_keys)
                if p_budget <= 0:
                    st.session_state.deduct_error = (
                        f"Pre-booked costs ${total_paid:,.0f} meet or exceed your "
                        f"${total:,.0f} budget — nothing left to plan. "
                        "Enter lower amounts or skip the deduction."
                    )
                    st.rerun()
                else:
                    st.session_state.deduct_error = None
                    _assistant(question)
                    _user(f"Paid: {details}. Remaining to plan: ${p_budget:,.0f}")
                    st.session_state.already_spent  = amounts
                    st.session_state.planning_budget = p_budget
                    st.session_state.budget_setup_step = "split"
                    st.rerun()

    # ── Sub-step: split ───────────────────────────────────────────────────────
    elif step == "split":
        p_budget    = st.session_state.planning_budget or total
        booked_set  = set(booked_keys)
        active_keys = [k for k in ("flights", "hotel", "attractions", "transport") if k not in booked_set]

        active_weight_total = sum(_ACTIVE_WEIGHTS[k] for k in active_keys)
        defaults = {k: round(_ACTIVE_WEIGHTS[k] * 100 / active_weight_total) for k in active_keys}

        question = f"How would you like to split your **${p_budget:,.0f}** planning budget? (adjust % or press Confirm to use defaults)"
        with st.chat_message("assistant"):
            st.markdown(question)
            if booked_set:
                st.markdown(f"*({', '.join(k.title() for k in booked_set)} pre-booked — excluded from split)*")

        with st.form("budget_split_form"):
            cols = st.columns([2, 2, 2, 3])
            cols[0].markdown("**Category**")
            cols[1].markdown("**Status**")
            cols[2].markdown("**%**")
            cols[3].markdown("**Amount**")

            entered: dict[str, int] = {}

            for row_key, label in _ALL_ROWS:
                cols = st.columns([2, 2, 2, 3])
                if row_key in booked_set:
                    cols[0].write(label)
                    cols[1].write("Pre-booked")
                    cols[2].write("—")
                    cols[3].write("—")
                else:
                    default_pct = defaults.get(row_key, 0)
                    cols[0].write(label)
                    cols[1].write("To plan")
                    val = cols[2].number_input(
                        "pct", min_value=0, max_value=100,
                        value=default_pct, step=5,
                        label_visibility="collapsed",
                        key=f"split_{row_key}",
                    )
                    entered[row_key] = val
                    cols[3].write(f"${p_budget * val / 100:,.0f}")

            confirmed_form = st.form_submit_button("Confirm Split")
            if confirmed_form:
                active_sum = sum(entered.values())
                if active_sum != 100:
                    scale = 100 / max(active_sum, 1)
                    for k in list(entered):
                        entered[k] = round(entered[k] * scale)

                fractions = {key: entered.get(key, 0) / 100 for key, _ in _ALL_ROWS}

                summary_lines = []
                for key, label in _ALL_ROWS:
                    if key in booked_set:
                        summary_lines.append(f"  {label}: Pre-booked")
                    else:
                        pct = fractions[key] * 100
                        summary_lines.append(f"  {label}: {pct:.0f}% → ${p_budget * fractions[key]:,.0f}")

                _assistant(question)
                _user("Budget split confirmed:\n" + "\n".join(summary_lines))
                st.session_state.split_fractions  = fractions
                st.session_state.planning_budget  = p_budget
                st.session_state.budget_done      = True
                st.session_state.stage            = "followup"
                st.rerun()


def _stage_followup() -> None:
    _render_history()

    info = st.session_state.info

    # If budget was just obtained during followup and we haven't done budget setup yet
    if info.get("total_budget") and not st.session_state.budget_done:
        st.session_state.total_budget = float(info["total_budget"])
        _go_to_budget_setup()
        st.rerun()
        return

    missing = missing_fields(info)

    if not missing or st.session_state.followup_count >= 4:
        st.session_state.stage = "summary"
        st.rerun()
        return

    if st.session_state.followup_question is None:
        with st.spinner("Thinking of what to ask…"):
            q = follow_up(info, missing)
        st.session_state.followup_question = q

    q = st.session_state.followup_question
    with st.chat_message("assistant"):
        st.markdown(q)

    if response := st.chat_input("Your answer…"):
        _assistant(q)
        _user(response)
        with st.spinner("Processing…"):
            new_info = extract_fields(response)
        st.session_state.info = merge_info(info, new_info)
        st.session_state.followup_count   += 1
        st.session_state.followup_question = None
        st.rerun()


def _stage_summary(rag_stores: dict) -> None:
    _render_history()

    info        = st.session_state.info
    p_budget    = st.session_state.planning_budget or st.session_state.total_budget or 0
    confirmed   = st.session_state.confirmed_booked
    booked_lbls = [_LABELS[k] for k in ("flights", "hotel", "transport") if confirmed.get(k)]

    summary = (
        f"Here's your trip summary:\n\n"
        f"- **From:** {info.get('origin', '?')}\n"
        f"- **To:** {info.get('destination', '?')}\n"
        f"- **Dates:** {info.get('start_date', '?')} → {info.get('end_date', '?')}\n"
        f"- **Planning budget:** ${p_budget:,.0f}"
        + (f"  *(pre-booked: {', '.join(booked_lbls)})*" if booked_lbls else "")
        + f"\n- **Interests:** {', '.join(info.get('interests') or []) or 'General sightseeing'}\n\n"
        f"Ready to build your complete itinerary?"
    )

    with st.chat_message("assistant"):
        st.markdown(summary)

    if st.button("Start Planning", type="primary", key="start_planning"):
        _assistant(summary)
        _user("Yes, let's start planning!")
        # Reset planning state
        st.session_state.trace_sink     = []
        st.session_state.result_box     = {}
        st.session_state.planning_thread = None
        st.session_state.stage          = "planning"
        _start_planning_thread(rag_stores)
        st.rerun()


def _stage_planning() -> None:
    _render_history()

    thread = st.session_state.planning_thread

    with st.chat_message("assistant"):
        traces = st.session_state.trace_sink

        if not traces:
            st.info("Initialising agents…")
        else:
            running = [e for e in traces if e["status"] == "running"]
            done    = [e for e in traces if e["status"] in ("done", "skipped")]
            agent_labels = {
                "flights": "Flight Agent", "attractions": "Attractions Agent",
                "hotel": "Hotel Agent", "transport": "Transport Agent",
                "coordinator": "Coordinator",
            }
            if running:
                st.info(f"Running: **{agent_labels.get(running[-1]['agent'], running[-1]['agent'])}**")
            st.progress(min(len(done) / 4, 1.0), text=f"{len(done)}/4 agents complete")

    if thread and not thread.is_alive():
        if "error" in st.session_state.result_box:
            err = st.session_state.result_box["error"]
            st.error(f"Planning failed: {err}")
            if st.button("Try again"):
                st.session_state.stage = "summary"
                st.rerun()
        else:
            st.session_state.itinerary = st.session_state.result_box.get("result")
            _assistant("Your complete travel itinerary is ready!")
            st.session_state.stage = "done"
            st.rerun()
    elif thread and thread.is_alive():
        time.sleep(0.5)
        st.rerun()


def _stage_done() -> None:
    _render_history()

    itinerary = st.session_state.itinerary
    if not itinerary:
        st.warning("No itinerary found.")
        return

    with st.chat_message("assistant"):
        st.markdown("### Your Complete Travel Itinerary")

        # Flight summary
        flight = itinerary.get("flights")
        if flight and not flight.get("_skipped"):
            st.markdown("**Flights**")
            recs = flight.get("recommended_flights", [])
            cost = flight.get("cost", 0)
            if recs:
                f = recs[0]
                airline  = f.get("airline", "?")
                origin   = f.get("departure") or f.get("origin", "?")
                dest     = f.get("arrival") or f.get("destination", "?")
                price    = f.get("price") or cost
                st.markdown(f"- {airline} · {origin} → {dest} · ${price:,.0f}")
            elif cost:
                st.markdown(f"- Cost: ${cost:,.0f}")
            else:
                st.markdown("- Flight details unavailable")

        # Attractions summary
        attractions = itinerary.get("attractions")
        if attractions:
            clusters = attractions.get("clusters", [])
            st.markdown(f"**Attractions** — {len(clusters)} day clusters")
            for i, cluster in enumerate(clusters, 1):
                name = cluster.get("cluster_name") or cluster.get("name") or f"Day {i}"
                places = cluster.get("attractions") or cluster.get("places") or []
                if places:
                    st.markdown(f"  - {name}: {', '.join(str(p.get('name', p)) if isinstance(p, dict) else str(p) for p in places[:3])}")

        # Hotel summary
        hotel = itinerary.get("hotel")
        if hotel and not hotel.get("_skipped"):
            h = hotel.get("recommended_hotel", {})
            if h:
                st.markdown(
                    f"**Hotel** — {h.get('name', '?')}, {h.get('location', '?')} "
                    f"· ${h.get('price_per_night', 0):,.0f}/night · Total: ${h.get('total_cost', 0):,.0f}"
                )

        # Transport summary
        transport = itinerary.get("transport")
        if transport and not transport.get("_skipped"):
            st.markdown(
                f"**Transport** — Total: ${transport.get('total_cost', 0):,.0f}"
            )
            tips = transport.get("tips", [])
            for tip in tips[:3]:
                st.markdown(f"  - {tip}")

        # Budget summary
        spent  = itinerary.get("total_spent", 0)
        budget = itinerary.get("total_budget", 0)
        within = itinerary.get("within_budget", True)
        status = "within budget" if within else "over budget"
        st.markdown(f"\n**Budget:** ${spent:,.2f} of ${budget:,.0f} — *{status}*")

    # st.expander + st.json must live outside st.chat_message to render correctly
    with st.expander("Raw itinerary (JSON)"):
        st.json(itinerary)

    if st.button("Plan another trip", key="restart"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ── Main dispatcher ───────────────────────────────────────────────────────────

def render_chat(rag_stores: dict) -> None:
    stage = st.session_state.stage

    if stage == "input":
        _stage_input()
    elif stage == "confirm_bookings":
        _stage_confirm_bookings()
    elif stage == "budget_setup":
        _stage_budget_setup()
    elif stage == "followup":
        _stage_followup()
    elif stage == "summary":
        _stage_summary(rag_stores)
    elif stage == "planning":
        _stage_planning()
    elif stage == "done":
        _stage_done()
    else:
        st.error(f"Unknown stage: {stage!r}")
