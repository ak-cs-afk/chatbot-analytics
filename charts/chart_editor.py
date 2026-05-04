from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from charts.chart_view import (
    ALL_TYPES,
    ALLOWED_FILTER_OPS,
    SINGLE_MEASURE_TYPES,
    ChartView,
    ChartViewError,
    ChartViewFilter,
    apply,
    default_chart_view,
)
from charts.renderer import ChartSpecError, render as render_figure
from charts.units import (
    CURRENCY_UNITS,
    FORMAT_LABELS,
    currency_label,
    format_to_unit,
    unit_to_format,
)
from features.loader import ColumnMeta, Feature


def render_chart_editor(
    chart_view: ChartView,
    df: pd.DataFrame,
    feature_columns: dict[str, ColumnMeta],
    feature: Feature,
    recipe_chart: dict | None,
    key_prefix: str,
) -> ChartView:
    """Render editor controls. Returns the updated chart_view (live)."""

    title = st.text_input(
        "Title",
        value=chart_view.title,
        key=f"{key_prefix}_title",
    )
    subtitle = st.text_input(
        "Subtitle",
        value=chart_view.subtitle,
        placeholder="Optional short description",
        key=f"{key_prefix}_subtitle",
    )

    type_col, x_col = st.columns(2)
    with type_col:
        ctype = st.selectbox(
            "Type",
            options=sorted(ALL_TYPES),
            index=sorted(ALL_TYPES).index(chart_view.type),
            key=f"{key_prefix}_type",
        )
    with x_col:
        x_options = [
            c for c, m in feature_columns.items()
            if m.kind == "dimension" or ctype == "scatter"
        ]
        if not x_options:
            x_options = list(feature_columns.keys())
        x_idx = x_options.index(chart_view.x) if chart_view.x in x_options else 0
        x = st.selectbox("X axis", options=x_options, index=x_idx, key=f"{key_prefix}_x")

    measure_options = [c for c, m in feature_columns.items() if m.kind == "measure"]
    if not measure_options:
        measure_options = list(feature_columns.keys())

    is_single = ctype in SINGLE_MEASURE_TYPES
    if is_single:
        cur = chart_view.y[0] if chart_view.y else measure_options[0]
        y_idx = measure_options.index(cur) if cur in measure_options else 0
        y_single = st.selectbox(
            "Y measure",
            options=measure_options,
            index=y_idx,
            key=f"{key_prefix}_y_single",
        )
        y = [y_single]
    else:
        default_y = [c for c in chart_view.y if c in measure_options] or [measure_options[0]]
        y = st.multiselect(
            "Y measures",
            options=measure_options,
            default=default_y,
            key=f"{key_prefix}_y_multi",
        )
        if not y:
            st.warning("At least one Y measure is required; reverting.")
            y = default_y

    color: str | None = None
    if len(y) == 1:
        dimension_options = ["(none)"] + [c for c, m in feature_columns.items() if m.kind == "dimension"]
        cur_color = chart_view.color or "(none)"
        c_idx = dimension_options.index(cur_color) if cur_color in dimension_options else 0
        color_choice = st.selectbox(
            "Color (group by)",
            options=dimension_options,
            index=c_idx,
            key=f"{key_prefix}_color",
        )
        color = None if color_choice == "(none)" else color_choice

    # Sort by + direction
    sort_options = ["(none)"] + list(df.columns)
    cur_sort = chart_view.sort_by or "(none)"
    sort_idx = sort_options.index(cur_sort) if cur_sort in sort_options else 0
    sort_col, dir_col = st.columns(2)
    with sort_col:
        sort_choice = st.selectbox(
            "Sort by",
            options=sort_options,
            index=sort_idx,
            key=f"{key_prefix}_sort_by",
        )
        sort_by_val = None if sort_choice == "(none)" else sort_choice
    with dir_col:
        dir_options = ["Desc", "Asc"]
        cur_dir = "Asc" if chart_view.sort_dir == "asc" else "Desc"
        dir_idx = dir_options.index(cur_dir)
        dir_choice = st.selectbox(
            "Direction",
            options=dir_options,
            index=dir_idx,
            key=f"{key_prefix}_sort_dir",
        )
        sort_dir_val = "asc" if dir_choice == "Asc" else "desc"

    referenced_cols = [x] + y
    st.markdown("**Column display labels**")
    column_labels: dict[str, str] = {}
    for col in referenced_cols:
        default = chart_view.column_labels.get(col, "")
        new_label = st.text_input(
            f"Label for `{col}`",
            value=default,
            placeholder=feature_columns[col].label if col in feature_columns else col,
            key=f"{key_prefix}_label_{col}",
        )
        if new_label.strip():
            column_labels[col] = new_label.strip()

    st.markdown("**Column format**")
    column_units: dict[str, str] = {}
    currency_codes = sorted(CURRENCY_UNITS)
    for col in y:
        default_unit = (
            chart_view.column_units.get(col)
            or (feature_columns[col].unit if col in feature_columns else "number")
        )
        current_fmt, current_cur = unit_to_format(default_unit)

        fmt = st.selectbox(
            f"Format for `{col}`",
            options=FORMAT_LABELS,
            index=FORMAT_LABELS.index(current_fmt),
            key=f"{key_prefix}_fmt_{col}",
        )
        if fmt == "Currency":
            default_currency = current_cur if current_cur in CURRENCY_UNITS else "usd"
            cur_idx = currency_codes.index(default_currency)
            currency_choice = st.selectbox(
                f"Currency for `{col}`",
                options=currency_codes,
                index=cur_idx,
                format_func=currency_label,
                key=f"{key_prefix}_cur_{col}",
            )
            chosen = format_to_unit(fmt, currency_choice)
        else:
            chosen = format_to_unit(fmt)

        feature_default = feature_columns[col].unit if col in feature_columns else None
        if chosen != feature_default:
            column_units[col] = chosen

    st.markdown("**Filters (post-execution, AND'd)**")
    filters = _render_filters(chart_view.filters, df, key_prefix)

    return ChartView(
        title=title,
        type=ctype,
        x=x,
        y=y,
        color=color,
        column_labels=column_labels,
        column_units=column_units,
        filters=filters,
        subtitle=subtitle,
        sort_by=sort_by_val,
        sort_dir=sort_dir_val,
    )


def _add_filter_callback(state_key: str, default_column: str) -> None:
    current = list(st.session_state.get(state_key, []))
    current.append({"column": default_column, "op": "==", "value": ""})
    st.session_state[state_key] = current


def _remove_filter_callback(state_key: str, index: int) -> None:
    current = list(st.session_state.get(state_key, []))
    if 0 <= index < len(current):
        current.pop(index)
        st.session_state[state_key] = current


def _render_filters(
    existing: list[ChartViewFilter],
    df: pd.DataFrame,
    key_prefix: str,
) -> list[ChartViewFilter]:
    state_key = f"{key_prefix}_filters_state"
    if state_key not in st.session_state:
        st.session_state[state_key] = [f.to_dict() for f in existing]

    column_options = list(df.columns)
    default_column = column_options[0] if column_options else ""

    # Add button first; uses on_click so state mutates BEFORE the rerun (no
    # st.rerun() needed, which would otherwise dismiss the parent dialog).
    st.button(
        "+ Add filter",
        key=f"{key_prefix}_filt_add",
        on_click=_add_filter_callback,
        args=(state_key, default_column),
    )

    new_state: list[dict] = []

    filters_list = list(st.session_state[state_key])
    if filters_list:
        hdr = st.columns([2, 1, 2, 0.5])
        hdr[0].caption("Column")
        hdr[1].caption("Op")
        hdr[2].caption("Value")

    for i, flt in enumerate(filters_list):
        cols = st.columns([2, 1, 2, 0.5])
        with cols[0]:
            col_idx = column_options.index(flt["column"]) if flt.get("column") in column_options else 0
            col = st.selectbox(
                f"Column {i + 1}",
                options=column_options,
                index=col_idx,
                key=f"{key_prefix}_filt_col_{i}",
                label_visibility="collapsed",
            )
        with cols[1]:
            op_options = sorted(ALLOWED_FILTER_OPS)
            op_idx = op_options.index(flt.get("op", "==")) if flt.get("op") in op_options else 0
            op = st.selectbox(
                f"Op {i + 1}",
                options=op_options,
                index=op_idx,
                key=f"{key_prefix}_filt_op_{i}",
                label_visibility="collapsed",
            )
        with cols[2]:
            cur_val = flt.get("value", "")
            if isinstance(cur_val, (list, tuple)):
                cur_val_str = ",".join(str(v) for v in cur_val)
            else:
                cur_val_str = str(cur_val) if cur_val is not None else ""
            val_str = st.text_input(
                f"Value {i + 1}",
                value=cur_val_str,
                key=f"{key_prefix}_filt_val_{i}",
                label_visibility="collapsed",
            )
            value: Any = _parse_filter_value(val_str, op)
        with cols[3]:
            st.button(
                "x",
                key=f"{key_prefix}_filt_rm_{i}",
                on_click=_remove_filter_callback,
                args=(state_key, i),
            )

        new_state.append({"column": col, "op": op, "value": value})

    st.session_state[state_key] = new_state

    # Convert to ChartViewFilter, skipping invalid rows.
    out: list[ChartViewFilter] = []
    for entry in new_state:
        if not entry.get("column") or entry.get("value") in (None, ""):
            continue
        try:
            out.append(ChartViewFilter.from_dict(entry))
        except ChartViewError:
            continue
    return out


def _parse_filter_value(raw: str, op: str) -> Any:
    raw = raw.strip()
    if op == "in":
        return [_coerce(v.strip()) for v in raw.split(",") if v.strip()]
    if op == "between":
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 2:
            return raw  # invalid; will be caught by validator
        return [_coerce(parts[0]), _coerce(parts[1])]
    return _coerce(raw)


def _coerce(s: str) -> Any:
    if not s:
        return s
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# ---------- Shared dialog ----------

@st.dialog("Edit Chart", width="large")
def open_chart_editor_dialog(
    view_state_key: str,
    data_columnar: dict | None,
    feature_id: str | None,
    recipe_chart: dict | None,
    key_prefix: str,
    save_pending_key: str,
    save_label: str = "Save to Dashboard",
) -> None:
    """Full-screen dialog for the chart editor with live preview.

    Widget interactions rerun the dialog only (not the full app), giving a
    live preview. Clicking Reset / Apply / Save closes the dialog via st.rerun()
    and signals the parent via save_pending_key in session state.
    """
    view: ChartView | None = st.session_state.get(view_state_key)
    if view is None:
        st.error("Chart view not found in session. Close and reopen the editor.")
        return

    # Each time the edit button is pressed, the caller increments this counter.
    # Using the generation in every widget key guarantees Streamlit treats all
    # widgets as new (initialises from index=/value=) rather than restoring
    # whatever the user typed in the previous unsaved session.
    gen = st.session_state.get(f"{key_prefix}_dlg_gen", 0)
    editor_key_prefix = f"dlg_{key_prefix}_g{gen}"

    # Clean up the previous generation's widget state to avoid session bloat.
    prev_prefix = f"dlg_{key_prefix}_g{gen - 1}_"
    for k in [k for k in st.session_state if k.startswith(prev_prefix)]:
        del st.session_state[k]

    from features.loader import load_features
    catalog = load_features()
    feature = catalog.get(feature_id) if feature_id else None
    feature_columns = feature.columns if feature else {}

    df = (
        pd.DataFrame(data_columnar["rows"], columns=data_columnar["columns"])
        if data_columnar
        else pd.DataFrame()
    )

    chart_col, editor_col = st.columns([3, 2])

    # Render editor first inside a scrollable container so new_view is available.
    with editor_col:
        with st.container(height=520, border=False):
            new_view = render_chart_editor(
                chart_view=view,
                df=df,
                feature_columns=feature_columns,
                feature=feature,
                recipe_chart=recipe_chart,
                key_prefix=editor_key_prefix,
            )

    invalid_msg: str | None = None
    fig = None
    try:
        filtered_df, hints = apply(new_view, df, feature_columns)
        fig = render_figure(new_view, filtered_df, hints)
    except (ChartViewError, ChartSpecError) as exc:
        invalid_msg = str(exc)

    with chart_col:
        if invalid_msg:
            st.error(f"Cannot render: {invalid_msg}")
        elif fig is not None:
            st.plotly_chart(fig, use_container_width=True)
        st.divider()
        btn_cols = st.columns(2)
        with btn_cols[0]:
            if st.button("Reset", key=f"dlg_reset_{key_prefix}", use_container_width=True):
                if feature:
                    st.session_state[view_state_key] = default_chart_view(
                        feature, recipe_chart=recipe_chart
                    )
                st.rerun()
        with btn_cols[1]:
            if st.button(
                save_label,
                key=f"dlg_save_{key_prefix}",
                disabled=bool(invalid_msg),
                type="primary",
                use_container_width=True,
            ):
                st.session_state[view_state_key] = new_view
                st.session_state[save_pending_key] = new_view.to_dict()
                st.rerun()