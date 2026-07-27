"""Reusable Streamlit components for traces and session display."""
from __future__ import annotations

import streamlit as st
from src.ui.log_buffer import get_lines as _get_log_lines

_AGENT_LABELS = {
    "flights":      "Flight Agent — LangGraph",
    "attractions":  "Attractions Agent — LangGraph",
    "hotel":        "Hotel Agent — CrewAI",
    "transport":    "Transport Agent — CrewAI",
    "coordinator":  "Coordinator — Google ADK",
}

_STATUS_LABELS = {
    "running":  "Running",
    "done":     "Done",
    "skipped":  "Skipped",
    "error":    "Error",
    "complete": "Complete",
}

# Vibrant accent colors — readable on both dark and light backgrounds.
# Used only for the left border and badge; card body uses CSS theme variables.
_STATUS_ACCENT = {
    "running":  "#F59E0B",  # amber
    "done":     "#10B981",  # emerald
    "skipped":  "#06B6D4",  # cyan
    "error":    "#EF4444",  # red
    "complete": "#10B981",  # emerald
}


def render_traces() -> None:
    traces = st.session_state.get("trace_sink", [])
    stage  = st.session_state.get("stage", "input")

    if not traces:
        if stage in ("planning", "done"):
            st.info("Waiting for agents to start…")
        else:
            st.info("Agent traces will appear here once planning starts.")
        return

    st.markdown("### Agent Execution Timeline")

    for event in traces:
        agent  = event.get("agent", "unknown")
        status = event.get("status", "")
        ts     = event.get("ts", "")[:19].replace("T", " ")
        badge  = _STATUS_LABELS.get(status, status.upper())
        accent = _STATUS_ACCENT.get(status, "#888888")
        label  = _AGENT_LABELS.get(agent, agent.title())

        detail = ""
        if status == "done":
            if "cost" in event:
                detail = f" &mdash; cost: <strong>${event['cost']:,.2f}</strong>"
            elif "clusters" in event:
                detail = f" &mdash; <strong>{event['clusters']} clusters</strong> planned"
            elif "name" in event:
                detail = f" &mdash; <strong>{event['name']}</strong>"
        elif status == "skipped":
            detail = " &mdash; pre-booked, skipped"
        elif status == "complete":
            detail = " &mdash; all agents finished"

        # Card uses CSS theme variables so it adapts to dark / light theme automatically.
        # Only the left border and badge use hardcoded accent colors.
        st.markdown(
            f"""<div style="
                background: var(--secondary-background-color);
                border-left: 4px solid {accent};
                border-radius: 4px;
                padding: 10px 14px;
                margin-bottom: 8px;
                font-size: 0.9rem;
                color: var(--text-color);
            ">
                <span style="
                    background: {accent}22;
                    color: {accent};
                    font-weight: 700;
                    font-size: 0.7rem;
                    text-transform: uppercase;
                    letter-spacing: 0.07em;
                    padding: 2px 7px;
                    border-radius: 3px;
                ">{badge}</span>
                &nbsp;
                <strong style="color: var(--text-color);">{label}</strong>{detail}
                <span style="
                    float: right;
                    color: var(--text-color);
                    opacity: 0.45;
                    font-size: 0.8rem;
                ">{ts}</span>
            </div>""",
            unsafe_allow_html=True,
        )

    if stage == "planning":
        st.markdown("---")
        running = [e for e in traces if e.get("status") == "running"]
        if running:
            st.info(
                f"Currently running: **{_AGENT_LABELS.get(running[-1]['agent'], running[-1]['agent'])}**"
            )

    # ── Raw log panel ─────────────────────────────────────────────────────────
    log_lines = _get_log_lines()
    if log_lines:
        st.markdown("---")
        with st.expander("Raw Logs", expanded=(stage == "planning")):
            st.code("\n".join(log_lines), language="text")


def render_session() -> None:
    info      = st.session_state.get("info", {})
    itinerary = st.session_state.get("itinerary")

    if not info.get("origin"):
        st.info("Session details will appear once you start planning.")
        return

    st.markdown("### Trip Overview")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("From", info.get("origin", "—"))
        st.metric("To",   info.get("destination", "—"))
    with col2:
        st.metric("Start", info.get("start_date", "—"))
        st.metric("End",   info.get("end_date", "—"))

    interests = info.get("interests") or []
    if interests:
        st.markdown(f"**Interests:** {', '.join(interests)}")

    total_budget    = st.session_state.get("total_budget")
    planning_budget = st.session_state.get("planning_budget")

    if total_budget:
        st.markdown("---")
        st.markdown("### Budget")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Budget", f"${total_budget:,.0f}")
        with col2:
            if planning_budget and planning_budget != total_budget:
                st.metric("Planning Budget", f"${planning_budget:,.0f}")

    confirmed    = st.session_state.get("confirmed_booked", {})
    booked_items = [k.title() for k, v in confirmed.items() if v]
    if booked_items:
        st.success(f"Pre-booked: {', '.join(booked_items)}")

    if itinerary:
        st.markdown("---")
        st.markdown("### Cost Breakdown")
        breakdown = itinerary.get("budget_breakdown", {})
        if breakdown:
            cols = st.columns(len(breakdown))
            for i, (k, v) in enumerate(breakdown.items()):
                cols[i].metric(k.title(), f"${v:,.0f}")

        spent  = itinerary.get("total_spent", 0)
        budget = itinerary.get("total_budget", 0)
        within = itinerary.get("within_budget", True)
        st.markdown("---")
        st.metric(
            "Total Spent",
            f"${spent:,.2f}",
            delta=f"${budget - spent:,.2f} remaining" if within else f"${spent - budget:,.2f} over",
            delta_color="normal" if within else "inverse",
        )

        sid = itinerary.get("session_id", "")
        if sid:
            st.caption(f"Session ID: `{sid}`")
