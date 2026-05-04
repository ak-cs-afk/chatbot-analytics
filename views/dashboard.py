from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from charts.chart_editor import open_chart_editor_dialog
from charts.chart_view import ChartView, ChartViewError, apply
from charts.renderer import ChartSpecError, render as render_figure
from charts.source_data import render_raw_data_expander
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    SavedChart,
    delete_chart,
    duplicate_chart,
    load_saved_charts,
    relative_time,
    update_chart_view,
)
from features.loader import load_features, reload_features


def render() -> None:
    reload_features()
    catalog = load_features()
    saved = sorted(
        load_saved_charts(SAVED_CHARTS_PATH),
        key=lambda c: c.created_at,
        reverse=True,
    )

    if not saved:
        st.info(
            "No saved charts yet. Ask the assistant a question, then click "
            "Save to Dashboard in the Edit dialog."
        )
        return

    st.caption(f"{len(saved)} saved chart(s). Charts auto-refresh from latest features data.")

    cols = st.columns(2)
    for i, sc in enumerate(saved):
        with cols[i % 2]:
            _render_tile(sc, catalog)


def _render_tile(sc: SavedChart, catalog: dict) -> None:
    key_prefix = f"saved_{sc.id}"
    view_state_key = f"{key_prefix}_view"
    update_pending_key = f"{key_prefix}_update_pending"

    if view_state_key not in st.session_state:
        try:
            st.session_state[view_state_key] = ChartView.from_dict(sc.chart_view)
        except ChartViewError as exc:
            container = st.container(border=True)
            with container:
                st.error(f"Saved chart has an invalid chart_view: {exc}")
                if st.button("Delete", key=f"{key_prefix}_delete_invalid"):
                    delete_chart(sc.id, SAVED_CHARTS_PATH)
                    st.rerun()
            return

    if update_pending_key in st.session_state:
        pending_dict = st.session_state.pop(update_pending_key)
        update_chart_view(sc.id, pending_dict, SAVED_CHARTS_PATH)
        st.toast("Chart updated.")

    view: ChartView = st.session_state[view_state_key]

    recipe_error: str | None = None
    result = None
    try:
        recipe = Recipe.from_dict(sc.recipe)
        result = execute(recipe, catalog)
    except (RecipeValidationError, RecipeExecutionError) as exc:
        recipe_error = str(exc)

    container = st.container(border=True)
    with container:
        if recipe_error:
            st.error(f"Could not refresh: {recipe_error}")
            with st.expander("View saved recipe", expanded=False):
                st.code(json.dumps(sc.recipe, indent=2), language="json")
            if st.button("Delete", key=f"{key_prefix}_delete_err"):
                delete_chart(sc.id, SAVED_CHARTS_PATH)
                st.rerun()
            return

        df = result.df
        feature_id = sc.recipe.get("sources", [None])[0]
        feature = catalog.get(feature_id)
        feature_columns = feature.columns if feature else {}

        title_col, edit_col, copy_col, del_col = st.columns([6, 1, 1, 1])
        with title_col:
            st.markdown(f"**{view.title}**")
            if view.subtitle:
                st.caption(view.subtitle)
        with edit_col:
            if st.button("✏️", key=f"{key_prefix}_edit_btn", use_container_width=True, help="Edit chart"):
                gen_key = f"{key_prefix}_dlg_gen"
                st.session_state[gen_key] = st.session_state.get(gen_key, 0) + 1
                live_columnar = {
                    "columns": list(df.columns),
                    "rows": df.values.tolist(),
                }
                open_chart_editor_dialog(
                    view_state_key=view_state_key,
                    data_columnar=live_columnar,
                    feature_id=feature_id,
                    recipe_chart=sc.recipe.get("chart"),
                    key_prefix=key_prefix,
                    save_pending_key=update_pending_key,
                    save_label="Save",
                )
        with copy_col:
            if st.button("⧉", key=f"{key_prefix}_copy_btn", use_container_width=True, help="Duplicate tile"):
                duplicate_chart(sc.id, SAVED_CHARTS_PATH)
                st.toast("Tile duplicated.")
                st.rerun()
        with del_col:
            if st.button("🗑️", key=f"{key_prefix}_delete_btn", use_container_width=True, help="Delete"):
                delete_chart(sc.id, SAVED_CHARTS_PATH)
                st.rerun()

        invalid_msg: str | None = None
        try:
            filtered_df, hints = apply(view, df, feature_columns)
            fig = render_figure(view, filtered_df, hints)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig")
        except (ChartViewError, ChartSpecError) as exc:
            invalid_msg = str(exc)
            st.error(f"Chart cannot render: {invalid_msg}")

        if result.sources_used:
            src = result.sources_used[0]
            st.caption(f"Source: {src['id']} ({src['name']}) - {len(df)} rows.")

        data_columnar = {"columns": list(df.columns), "rows": df.values.tolist()}
        render_raw_data_expander(data_columnar=data_columnar, name=view.title, key_suffix=key_prefix)

        st.caption(f"Updated {relative_time(sc.updated_at)}.")
