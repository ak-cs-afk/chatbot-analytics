from __future__ import annotations

from typing import Callable

import streamlit as st

from agent.tools import ChartMeta
from charts.source_data import render_raw_data_expander


def render_chart_with_actions(
    chart_meta: ChartMeta,
    message_index: int,
    on_save: Callable[[ChartMeta], None],
    on_rename: Callable[[ChartMeta, str], None],
    saved_keys: set[str],
    recipe_hash_fn: Callable[[dict], str],
) -> None:
    """Render a SAVABLE direct chart card.

    Source charts under derived analyses are rendered by charts/analysis_card.py
    and never reach this function.
    """
    if chart_meta.mode != "direct":
        st.warning(
            f"render_chart_with_actions called with non-direct chart "
            f"(mode={chart_meta.mode!r}). This is a bug; please report."
        )
        return

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

        # Methodology line: simple for direct charts (no transformations).
        if chart_meta.sources_used:
            src = chart_meta.sources_used[0]
            row_count = len(chart_meta.data_columnar["rows"]) if chart_meta.data_columnar else "?"
            st.caption(
                f"**Source:** `{src['id']}` ({src['name']}) - "
                f"{row_count} rows. Direct view (no transformations)."
            )

        render_raw_data_expander(
            data_columnar=chart_meta.data_columnar,
            name=chart_meta.name,
            key_suffix=f"direct_{message_index}_{chart_meta.chart_id}",
        )

        already_saved = recipe_hash_fn(chart_meta.recipe) in saved_keys
        cols = st.columns([4, 1])
        with cols[1]:
            if already_saved:
                st.button("Saved", key=save_key, disabled=True, use_container_width=True)
            else:
                if st.button("Save to Dashboard", key=save_key, use_container_width=True):
                    on_save(chart_meta)
                    st.rerun()
