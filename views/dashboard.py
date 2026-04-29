from __future__ import annotations

import streamlit as st

from charts.renderer import ChartSpecError, spec_to_figure
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    SavedChart,
    delete_chart,
    load_saved_charts,
    rename_chart,
    _spec_hash,
)
from features.loader import (
    FeatureNotFoundError,
    load_features,
    reload_features,
)


def render() -> None:
    # Always pull fresh feature data so the "latest data" guarantee holds.
    reload_features()
    catalog = load_features()
    saved = sorted(load_saved_charts(SAVED_CHARTS_PATH), key=lambda c: c.created_at, reverse=True)

    if not saved:
        st.info("No saved charts yet. Ask the assistant a question, then click Save to Dashboard on a chart.")
        return

    st.caption(f"{len(saved)} saved chart(s). Charts auto-refresh from latest features data.")

    cols = st.columns(2)
    for i, sc in enumerate(saved):
        with cols[i % 2]:
            _render_tile(sc, catalog)


def _render_tile(sc: SavedChart, catalog: dict) -> None:
    container = st.container(border=True)
    with container:
        feature = catalog.get(sc.feature_id)

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

        if feature is None:
            st.warning(
                f"Source feature `{sc.feature_id}` no longer available. "
                "Delete this chart to remove it from the dashboard."
            )
        else:
            try:
                figure = spec_to_figure(sc.spec, feature.data_columnar)
                st.plotly_chart(figure, use_container_width=True, key=chart_key)
            except ChartSpecError as exc:
                st.warning(f"Chart cannot render with current data: {exc}")

        cols = st.columns([4, 1])
        with cols[1]:
            if st.button("Delete", key=delete_key, use_container_width=True):
                delete_chart(sc.id, SAVED_CHARTS_PATH)
                st.rerun()