from __future__ import annotations

import json

import streamlit as st

from agent.tools import AnalysisCard, ChartMeta
from charts.source_data import render_raw_data_expander


def render_analysis_card(
    card: AnalysisCard,
    source_charts: list[ChartMeta],
    message_index: int,
) -> None:
    """Render the analysis block (methodology + recipe expander) and its source charts."""
    # ---- Methodology block (always visible) ----
    container = st.container(border=True)
    with container:
        st.markdown("**Methodology**")

        sources_line = ", ".join(
            f"`{src['id']}` ({src['name']})" for src in card.sources_used
        )
        st.markdown(f"Sources: {sources_line}")

        for step in card.methodology_steps:
            st.markdown(f"{step['step']}. {step['text']}")

        with st.expander("View recipe (technical)", expanded=False):
            st.code(json.dumps(card.recipe, indent=2), language="json")

    # ---- Source charts grid (no Save button) ----
    if not source_charts:
        return

    if len(source_charts) >= 2:
        cols = st.columns(2)
        for i, cm in enumerate(source_charts):
            with cols[i % 2]:
                _render_source_chart(cm, message_index, card.analysis_id)
    else:
        _render_source_chart(source_charts[0], message_index, card.analysis_id)


def _render_source_chart(cm: ChartMeta, message_index: int, analysis_id: int) -> None:
    sub = st.container(border=True)
    with sub:
        st.markdown(f"**{cm.name}**")
        chart_key = f"src_fig_{message_index}_{analysis_id}_{cm.chart_id}"
        st.plotly_chart(cm.figure, use_container_width=True, key=chart_key)

        render_raw_data_expander(
            data_columnar=cm.data_columnar,
            name=cm.name,
            key_suffix=f"src_{message_index}_{analysis_id}_{cm.chart_id}",
        )
