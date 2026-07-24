"""Streamlit session state initialisation."""
from __future__ import annotations

import streamlit as st


def init_state() -> None:
    defaults: dict = {
        "stage": "input",
        "chat_history": [],         # [{"role": "user"|"assistant", "content": str}]
        "info": {},                  # extracted trip info dict
        # Booking confirmation
        "booked": {"flights": False, "hotel": False, "transport": False},
        "confirmed_booked": {"flights": False, "hotel": False, "transport": False},
        "confirm_items": [],         # list of keys needing confirmation
        "confirm_idx": 0,
        # Budget
        "total_budget": None,        # float — raw budget from info
        "planning_budget": None,     # float — after deductions
        "budget_done": False,        # True after budget_split step completes
        "budget_setup_step": "deduct_q",  # "deduct_q" | "deduct_amounts" | "split"
        "already_spent": {},         # {key: float} for pre-booked costs paid
        "deduct_error": None,        # validation error message for deduct_amounts step
        # Follow-up
        "followup_count": 0,
        "followup_question": None,
        # Planning
        "trace_sink": [],            # agent trace events appended by background thread
        "result_box": {},            # {"result": dict} or {"error": str}
        "planning_thread": None,
        "itinerary": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
