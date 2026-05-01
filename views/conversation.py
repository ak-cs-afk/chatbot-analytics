from __future__ import annotations

import streamlit as st

from agent.client import AnalyticsAgent, AssistantTurn, ProgressUpdate
from agent.recipe import recipe_hash
from agent.tools import AnalysisCard, ChartMeta
from charts.analysis_card import render_analysis_card
from charts.chart_actions import render_chart_with_actions
from charts.chart_view import ChartView
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    load_saved_charts,
    save_chart,
)


def render() -> None:
    _init_session_state()
    _render_history()
    _handle_input()


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    saved = load_saved_charts(SAVED_CHARTS_PATH)
    st.session_state.saved_chart_keys = {recipe_hash(sc.recipe) for sc in saved}


def _render_history() -> None:
    for index, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            steps = msg.get("reasoning_steps") or []
            if steps:
                _render_reasoning_trace(steps)
            if msg.get("text"):
                _safe_markdown(msg["text"])
            _render_analysis_and_charts(
                analysis_cards=msg.get("analysis_cards") or [],
                charts=msg.get("charts") or [],
                message_index=index,
            )


def _handle_input() -> None:
    user_input = st.chat_input("Ask about your data...")
    if not user_input:
        return

    st.session_state.messages.append(
        {"role": "user", "text": user_input, "charts": [], "analysis_cards": []}
    )
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
        except Exception as exc:  # noqa: BLE001
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

        if final_turn.reasoning_steps:
            _render_reasoning_trace(final_turn.reasoning_steps)
        if final_turn.text:
            _safe_markdown(final_turn.text)

        new_index = len(st.session_state.messages)
        _render_analysis_and_charts(
            analysis_cards=final_turn.analysis_cards,
            charts=final_turn.charts,
            message_index=new_index,
        )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "text": final_turn.text,
            "charts": final_turn.charts,
            "analysis_cards": final_turn.analysis_cards,
            "reasoning_steps": final_turn.reasoning_steps,
        }
    )


def _safe_markdown(text: str) -> None:
    """Render LLM text, escaping $ to prevent Streamlit LaTeX math-mode."""
    st.markdown(text.replace("$", r"\$"))


def _render_reasoning_trace(steps: list[dict]) -> None:
    with st.expander("Reasoning trace", expanded=False):
        for i, step in enumerate(steps):
            ok = step.get("ok", True)
            icon = "[ok]" if ok else "[fail]"
            label = step.get("label", step.get("tool", "?"))
            st.markdown(f"**{i + 1}. {icon} {label}**")
            detail = step.get("detail", "")
            if detail:
                st.caption(detail)


def _render_analysis_and_charts(
    analysis_cards: list[AnalysisCard],
    charts: list[ChartMeta],
    message_index: int,
) -> None:
    chart_by_id = {cm.chart_id: cm for cm in charts}
    chart_ids_in_cards: set[int] = set()
    for card in analysis_cards:
        chart_ids_in_cards.update(card.source_chart_ids)
        source_charts = [chart_by_id[cid] for cid in card.source_chart_ids if cid in chart_by_id]
        render_analysis_card(card, source_charts, message_index, on_save_source=_on_save_chart)

    standalone = [cm for cm in charts if cm.chart_id not in chart_ids_in_cards and cm.mode == "direct"]
    if not standalone:
        return

    if len(standalone) >= 3:
        cols = st.columns(2)
        for i, cm in enumerate(standalone):
            with cols[i % 2]:
                _render_one(cm, message_index)
    else:
        for cm in standalone:
            _render_one(cm, message_index)


def _render_one(cm: ChartMeta, message_index: int) -> None:
    render_chart_with_actions(
        chart_meta=cm,
        message_index=message_index,
        on_save=_on_save_chart,
        on_rename=_on_rename,
        saved_keys=st.session_state.saved_chart_keys,
    )


def _on_save_chart(cm: ChartMeta, view: ChartView) -> None:
    saved = save_chart(
        name=view.title,
        recipe=cm.recipe,
        chart_view=view.to_dict(),
        path=SAVED_CHARTS_PATH,
    )
    st.session_state.saved_chart_keys.add(recipe_hash(saved.recipe))
    cm.saved_id = saved.id
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