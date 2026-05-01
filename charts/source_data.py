from __future__ import annotations

import io

import pandas as pd
import streamlit as st


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to CSV bytes for st.download_button."""
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")


def render_raw_data_expander(
    data_columnar: dict | None,
    name: str,
    key_suffix: str,
    expanded: bool = False,
) -> None:
    """Render a 'Raw data' expander with sortable table + Download CSV button.

    Used by both direct chart cards (chart_actions.py) and derived-analysis source
    charts (analysis_card.py) so the UX stays identical.

    Args:
        data_columnar: {"columns": [...], "rows": [[...], ...]} or None.
        name: Used for the CSV filename.
        key_suffix: Unique suffix for Streamlit widget keys.
        expanded: Initial state of the expander.
    """
    with st.expander("Raw data", expanded=expanded):
        if data_columnar is None:
            st.info("Raw data not attached for this chart.")
            return
        df = pd.DataFrame(
            data_columnar["rows"],
            columns=data_columnar["columns"],
        )
        st.dataframe(df, height=300, use_container_width=True)
        st.download_button(
            label="Download CSV",
            data=dataframe_to_csv_bytes(df),
            file_name=f"{name.replace(' ', '_')}.csv",
            mime="text/csv",
            key=f"csv_{key_suffix}",
            use_container_width=True,
        )
