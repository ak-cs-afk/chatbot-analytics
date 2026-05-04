from __future__ import annotations

import json
from typing import Callable

import pandas as pd
import streamlit as st

from agent.tools import AnalysisCard, ChartMeta
from charts.chart_editor import open_chart_editor_dialog
from charts.chart_view import ChartView, ChartViewError, apply
from charts.renderer import ChartSpecError, render as render_figure
from charts.source_data import render_raw_data_expander
from features.loader import load_features


def render_analysis_card(
    card: AnalysisCard,
    source_charts: list[ChartMeta],
    message_index: int,
    on_save_source: Callable[[ChartMeta, ChartView], None],
    on_duplicate_source: Callable[[ChartMeta, ChartView], None],
    on_delete_source: Callable[[ChartMeta], None],
) -> None:
    """Render the analysis block (methodology + recipe expander) and its source charts."""
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

    if not source_charts:
        return

    if len(source_charts) >= 2:
        cols = st.columns(2)
        for i, cm in enumerate(source_charts):
            with cols[i % 2]:
                _render_source_chart(
                    cm, message_index, card.analysis_id,
                    on_save_source, on_duplicate_source, on_delete_source,
                )
    else:
        _render_source_chart(
            source_charts[0], message_index, card.analysis_id,
            on_save_source, on_duplicate_source, on_delete_source,
        )


def _render_source_chart(
    cm: ChartMeta,
    message_index: int,
    analysis_id: int,
    on_save: Callable[[ChartMeta, ChartView], None],
    on_duplicate: Callable[[ChartMeta, ChartView], None],
    on_delete: Callable[[ChartMeta], None],
) -> None:
    key_prefix = f"src_{message_index}_{analysis_id}_{cm.chart_id}"
    view_state_key = f"{key_prefix}_view"
    save_pending_key = f"{key_prefix}_save_pending"

    if view_state_key not in st.session_state:
        st.session_state[view_state_key] = ChartView.from_dict(cm.chart_view)

    if save_pending_key in st.session_state:
        pending_view = ChartView.from_dict(st.session_state.pop(save_pending_key))
        on_save(cm, pending_view)

    view: ChartView = st.session_state[view_state_key]

    catalog = load_features()
    feature_id = cm.recipe.get("sources", [None])[0]
    feature = catalog.get(feature_id) if feature_id else None
    if cm.data_columnar:
        df = pd.DataFrame(cm.data_columnar["rows"], columns=cm.data_columnar["columns"])
    else:
        df = pd.DataFrame()
    feature_columns = feature.columns if feature else {}

    sub = st.container(border=True)
    with sub:
        title_col, edit_col, copy_col, del_col = st.columns([6, 1, 1, 1])
        with title_col:
            st.markdown(f"**{view.title}**")
            if view.subtitle:
                st.caption(view.subtitle)
        with edit_col:
            if st.button("✏️", key=f"{key_prefix}_edit_btn", use_container_width=True, help="Edit chart"):
                gen_key = f"{key_prefix}_dlg_gen"
                st.session_state[gen_key] = st.session_state.get(gen_key, 0) + 1
                open_chart_editor_dialog(
                    view_state_key=view_state_key,
                    data_columnar=cm.data_columnar,
                    feature_id=feature_id,
                    recipe_chart=None,
                    key_prefix=key_prefix,
                    save_pending_key=save_pending_key,
                    save_label="Save to Dashboard",
                )
        with copy_col:
            if st.button("⧉", key=f"{key_prefix}_copy_btn", use_container_width=True, help="Copy to Dashboard"):
                on_duplicate(cm, view)
                st.toast("Copied to Dashboard.")
                st.rerun()
        with del_col:
            if st.button("🗑️", key=f"{key_prefix}_delete_btn", use_container_width=True, help="Delete"):
                on_delete(cm)
                st.rerun()

        invalid_msg: str | None = None
        try:
            filtered_df, hints = apply(view, df, feature_columns)
            fig = render_figure(view, filtered_df, hints)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig")
        except (ChartViewError, ChartSpecError) as exc:
            invalid_msg = str(exc)
            st.error(f"Chart cannot render: {invalid_msg}")

        render_raw_data_expander(
            data_columnar=cm.data_columnar,
            name=view.title,
            key_suffix=key_prefix,
        )
