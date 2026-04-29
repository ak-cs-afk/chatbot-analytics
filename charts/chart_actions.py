from __future__ import annotations

from typing import Callable

import streamlit as st

from agent.tools import ChartMeta


def render_chart_with_actions(
    chart_meta: ChartMeta,
    message_index: int,
    on_save: Callable[[ChartMeta], None],
    on_rename: Callable[[ChartMeta, str], None],
    saved_keys: set[tuple[str, str]],
    spec_hash_fn: Callable[[dict], str],
) -> None:
    """Render a chart with its action toolbar inside a Streamlit container.

    Args:
        chart_meta: The chart to render.
        message_index: Position of this turn in the message history (used for unique keys).
        on_save: Callback invoked when user clicks Save to Dashboard.
        on_rename: Callback invoked when user edits the name field (debounced by Streamlit's rerun).
        saved_keys: Set of (feature_id, spec_hash) tuples already in the dashboard.
        spec_hash_fn: Function that turns a spec dict into a stable hash string.
    """
    container = st.container(border=True)
    with container:
        name_key = f"chart_name_{message_index}_{chart_meta.chart_id}"
        save_key = f"chart_save_{message_index}_{chart_meta.chart_id}"
        chart_key = f"chart_fig_{message_index}_{chart_meta.chart_id}"

        new_name = st.text_input(
            "Chart name",
            value=chart_meta.name,
            key=name_key,
            label_visibility="collapsed",
        )
        if new_name != chart_meta.name:
            on_rename(chart_meta, new_name)

        st.plotly_chart(chart_meta.figure, use_container_width=True, key=chart_key)

        already_saved = (chart_meta.feature_id, spec_hash_fn(chart_meta.spec)) in saved_keys
        cols = st.columns([4, 1])
        with cols[1]:
            if already_saved:
                st.button("Saved ✓", key=save_key, disabled=True, use_container_width=True)
            else:
                if st.button("Save to Dashboard", key=save_key, use_container_width=True):
                    on_save(chart_meta)
                    st.rerun()