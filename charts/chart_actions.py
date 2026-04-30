from __future__ import annotations

import json
from typing import Callable

import streamlit as st

from agent.tools import ChartMeta


def render_chart_with_actions(
    chart_meta: ChartMeta,
    message_index: int,
    on_save: Callable[[ChartMeta], None],
    on_rename: Callable[[ChartMeta, str], None],
    saved_keys: set[str],
    recipe_hash_fn: Callable[[dict], str],
) -> None:
    """Render a chart with its action toolbar inside a Streamlit container.

    Args:
        chart_meta: The chart to render.
        message_index: Position of this turn in the message history (used for unique keys).
        on_save: Callback invoked when user clicks Save to Dashboard.
        on_rename: Callback invoked when user edits the name field.
        saved_keys: Set of recipe_hash strings already in the dashboard.
        recipe_hash_fn: Function that turns a recipe dict into a stable hash string.
    """
    container = st.container(border=True)
    with container:
        name_key = f"chart_name_{message_index}_{chart_meta.chart_id}"
        save_key = f"chart_save_{message_index}_{chart_meta.chart_id}"
        chart_key = f"chart_fig_{message_index}_{chart_meta.chart_id}"
        expand_key = f"chart_explain_{message_index}_{chart_meta.chart_id}"

        new_name = st.text_input(
            "Chart name",
            value=chart_meta.name,
            key=name_key,
            label_visibility="collapsed",
        )
        if new_name != chart_meta.name:
            on_rename(chart_meta, new_name)

        st.plotly_chart(chart_meta.figure, use_container_width=True, key=chart_key)

        with st.expander("How this was calculated", expanded=False):
            if chart_meta.sources_used:
                st.markdown("**Sources:**")
                for src in chart_meta.sources_used:
                    st.markdown(f"- `{src['id']}` - {src['name']}")
            st.markdown(f"**Method:** {chart_meta.recipe_text}")
            st.markdown("**Recipe:**")
            st.code(json.dumps(chart_meta.recipe, indent=2), language="json")

        already_saved = recipe_hash_fn(chart_meta.recipe) in saved_keys
        cols = st.columns([4, 1])
        with cols[1]:
            if already_saved:
                st.button("Saved", key=save_key, disabled=True, use_container_width=True)
            else:
                if st.button("Save to Dashboard", key=save_key, use_container_width=True):
                    on_save(chart_meta)
                    st.rerun()