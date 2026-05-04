from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from agent.recipe import recipe_hash
from agent.tools import ChartMeta
from charts.chart_editor import open_chart_editor_dialog
from charts.chart_view import ChartView, ChartViewError, apply
from charts.renderer import ChartSpecError, render as render_figure
from charts.source_data import render_raw_data_expander
from features.loader import load_features


def render_chart_with_actions(
    chart_meta: ChartMeta,
    message_index: int,
    on_save: Callable[[ChartMeta, ChartView], None],
    on_duplicate: Callable[[ChartMeta, ChartView], None],
    on_delete: Callable[[ChartMeta], None],
    saved_keys: set[str],
) -> None:
    """Render a savable direct chart card with Edit / Copy / Delete actions."""
    if chart_meta.mode != "direct":
        st.warning(
            f"render_chart_with_actions called with non-direct chart "
            f"(mode={chart_meta.mode!r}). This is a bug; please report."
        )
        return

    key_prefix = f"chart_{message_index}_{chart_meta.chart_id}"
    view_state_key = f"{key_prefix}_view"
    save_pending_key = f"{key_prefix}_save_pending"

    if view_state_key not in st.session_state:
        st.session_state[view_state_key] = ChartView.from_dict(chart_meta.chart_view)

    if save_pending_key in st.session_state:
        pending_view = ChartView.from_dict(st.session_state.pop(save_pending_key))
        on_save(chart_meta, pending_view)

    view: ChartView = st.session_state[view_state_key]

    feature_id = chart_meta.recipe.get("sources", [None])[0]
    catalog = load_features()
    feature = catalog.get(feature_id) if feature_id else None
    if chart_meta.data_columnar:
        df = pd.DataFrame(
            chart_meta.data_columnar["rows"],
            columns=chart_meta.data_columnar["columns"],
        )
    else:
        df = pd.DataFrame()
    feature_columns = feature.columns if feature else {}

    already_saved = recipe_hash(chart_meta.recipe) in saved_keys

    container = st.container(border=True)
    with container:
        title_col, save_col, edit_col, copy_col, del_col = st.columns([5, 1, 1, 1, 1])
        with title_col:
            st.markdown(f"**{view.title}**")
            if view.subtitle:
                st.caption(view.subtitle)
        with save_col:
            if st.button(
                "💾",
                key=f"{key_prefix}_save_btn",
                use_container_width=True,
                help="Saved to Dashboard" if already_saved else "Save to Dashboard",
                disabled=already_saved,
            ):
                on_save(chart_meta, view)
                st.rerun()
        with edit_col:
            if st.button("✏️", key=f"{key_prefix}_edit_btn", use_container_width=True, help="Edit chart"):
                gen_key = f"{key_prefix}_dlg_gen"
                st.session_state[gen_key] = st.session_state.get(gen_key, 0) + 1
                open_chart_editor_dialog(
                    view_state_key=view_state_key,
                    data_columnar=chart_meta.data_columnar,
                    feature_id=feature_id,
                    recipe_chart=chart_meta.recipe.get("chart"),
                    key_prefix=key_prefix,
                    save_pending_key=save_pending_key,
                    save_label="Save to Dashboard",
                )
        with copy_col:
            if st.button("⧉", key=f"{key_prefix}_copy_btn", use_container_width=True, help="Copy to Dashboard"):
                on_duplicate(chart_meta, view)
                st.toast("Copied to Dashboard.")
                st.rerun()
        with del_col:
            if st.button("🗑️", key=f"{key_prefix}_delete_btn", use_container_width=True, help="Delete"):
                on_delete(chart_meta)
                st.rerun()

        invalid_msg: str | None = None
        try:
            filtered_df, hints = apply(view, df, feature_columns)
            fig = render_figure(view, filtered_df, hints)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig")
        except (ChartViewError, ChartSpecError) as exc:
            invalid_msg = str(exc)
            st.error(f"Chart cannot render: {invalid_msg}")

        if chart_meta.sources_used:
            src = chart_meta.sources_used[0]
            row_count = len(chart_meta.data_columnar["rows"]) if chart_meta.data_columnar else "?"
            st.caption(f"Source: {src['id']} ({src['name']}) - {row_count} rows.")

        render_raw_data_expander(
            data_columnar=chart_meta.data_columnar,
            name=view.title,
            key_suffix=f"direct_{message_index}_{chart_meta.chart_id}",
        )
