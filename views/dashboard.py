from __future__ import annotations

import json

import streamlit as st

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    SavedChart,
    delete_chart,
    load_saved_charts,
    rename_chart,
)
from features.loader import load_features, reload_features


def render() -> None:
    # Always pull fresh feature data so the "latest data" guarantee holds.
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
            "Save to Dashboard on a chart."
        )
        return

    st.caption(f"{len(saved)} saved chart(s). Charts auto-refresh from latest features data.")

    cols = st.columns(2)
    for i, sc in enumerate(saved):
        with cols[i % 2]:
            _render_tile(sc, catalog)


def _render_tile(sc: SavedChart, catalog: dict) -> None:
    container = st.container(border=True)
    with container:
        name_key = f"saved_name_{sc.id}"
        delete_key = f"saved_delete_{sc.id}"
        chart_key = f"saved_fig_{sc.id}"

        new_name = st.text_input(
            "Chart name",
            value=sc.name,
            key=name_key,
            label_visibility="collapsed",
        )
        if new_name != sc.name:
            rename_chart(sc.id, new_name, SAVED_CHARTS_PATH)

        # Try to rebuild the figure from the saved recipe against latest data.
        error: str | None = None
        result = None
        try:
            recipe = Recipe.from_dict(sc.recipe)
            result = execute(recipe, catalog)
        except (RecipeValidationError, RecipeExecutionError) as exc:
            error = str(exc)

        if error:
            st.error(f"Could not refresh: {error}")
            with st.expander("View saved recipe", expanded=False):
                st.code(json.dumps(sc.recipe, indent=2), language="json")
        elif result is None or result.figure is None:
            st.warning("This saved chart has no chart spec - nothing to render.")
        else:
            st.plotly_chart(result.figure, use_container_width=True, key=chart_key)
            with st.expander("How this was calculated", expanded=False):
                if result.sources_used:
                    st.markdown("**Sources:**")
                    for src in result.sources_used:
                        st.markdown(f"- `{src['id']}` - {src['name']}")
                st.markdown(f"**Method:** {result.recipe_text}")
                st.markdown("**Recipe:**")
                st.code(json.dumps(sc.recipe, indent=2), language="json")

        cols = st.columns([4, 1])
        with cols[1]:
            if st.button("Delete", key=delete_key, use_container_width=True):
                delete_chart(sc.id, SAVED_CHARTS_PATH)
                st.rerun()