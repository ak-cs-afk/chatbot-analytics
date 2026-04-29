from __future__ import annotations

import streamlit as st

from agent.client import AnalyticsAgent, AssistantTurn, ProgressUpdate
from agent.tools import ChartMeta
from charts.chart_actions import render_chart_with_actions
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    load_saved_charts,
    save_chart,
    _spec_hash,  # internal but stable - used for keying
)


def render() -> None:
    _init_session_state()
    _render_history()
    _handle_input()


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        # Each message: {"role", "text", "charts": list[ChartMeta]}
        st.session_state.messages = []
    # Recompute saved keys from disk on first render.
    saved = load_saved_charts(SAVED_CHARTS_PATH)
    st.session_state.saved_chart_keys = {
        (sc.feature_id, _spec_hash(sc.spec)) for sc in saved
    }


def _render_history() -> None:
    for index, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg.get("text"):
                st.markdown(msg["text"])
            charts: list[ChartMeta] = msg.get("charts") or []
            _render_chart_list(charts, message_index=index)


def _handle_input() -> None:
    user_input = st.chat_input("Ask about your data...")
    if not user_input:
        return

    st.session_state.messages.append({"role": "user", "text": user_input, "charts": []})
    with st.chat_message("user"):
        st.markdown(user_input)

    agent = AnalyticsAgent()
    history = _to_chat_history(st.session_state.messages[:-1])

    with st.chat_message("assistant"):
        status = st.status("Thinking...", expanded=False)
        final_turn: AssistantTurn | None = None
        try:
            for update in agent.run_streaming(user_input, history):
                if isinstance(update, ProgressUpdate):
                    status.update(label=update.label)
                else:
                    final_turn = update
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Unexpected error: {exc}")
            return

        if final_turn is None:
            status.update(label="No response", state="error")
            return

        status.update(
            label="Done" if not final_turn.error else "Failed",
            state="error" if final_turn.error else "complete",
        )

        if final_turn.text:
            st.markdown(final_turn.text)

        new_index = len(st.session_state.messages)
        _render_chart_list(final_turn.charts, message_index=new_index)

    st.session_state.messages.append(
        {"role": "assistant", "text": final_turn.text, "charts": final_turn.charts}
    )


def _render_chart_list(charts: list[ChartMeta], message_index: int) -> None:
    if not charts:
        return
    if len(charts) >= 3:
        cols = st.columns(2)
        for i, cm in enumerate(charts):
            with cols[i % 2]:
                _render_one(cm, message_index)
    else:
        for cm in charts:
            _render_one(cm, message_index)


def _render_one(cm: ChartMeta, message_index: int) -> None:
    render_chart_with_actions(
        chart_meta=cm,
        message_index=message_index,
        on_save=_on_save,
        on_rename=_on_rename,
        saved_keys=st.session_state.saved_chart_keys,
        spec_hash_fn=_spec_hash,
    )


def _on_save(cm: ChartMeta) -> None:
    saved = save_chart(
        name=cm.name,
        feature_id=cm.feature_id,
        spec=cm.spec,
        path=SAVED_CHARTS_PATH,
    )
    st.session_state.saved_chart_keys.add((saved.feature_id, _spec_hash(saved.spec)))
    st.toast(f"Saved '{saved.name}' to Dashboard.")


def _on_rename(cm: ChartMeta, new_name: str) -> None:
    cm.name = new_name


def _to_chat_history(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        text = m.get("text") or ""
        if not text:
            continue
        out.append({"role": m["role"], "content": text})
    return out