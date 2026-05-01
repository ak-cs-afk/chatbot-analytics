# Chart Editor (v5) - Design

**Date:** 2026-05-01
**Status:** Approved (pending user spec review)
**Supersedes:** parts of `2026-04-30-derived-analysis-redesign-design.md` (renderer, saved-chart store schema, dashboard layout)

## Purpose

Five problems and feature requests:

1. **Wrong-`$` bug.** The renderer applies `$` formatting if any column in the dataframe ends with `_usd`, even when the chart's Y axis is a count column (e.g., F012's `orders` chart shows `$` because `gmv_usd` is in the dataframe).
2. **No chart editor.** Users cannot change a chart's title, type, axes, units, column display labels, or filters after the agent produces it.
3. **No multi-measure charts.** Users cannot plot multiple Y series on one chart (e.g., MRR + Churn on a dual-axis line chart).
4. **Dashboard "How this was computed" is unwanted.** Replace with the same Raw data expander used elsewhere.
5. **Saved-chart editing.** Users want to edit saved charts in the dashboard, not just create-and-pin.

This redesign introduces a layered visualization model: the recipe stays as the immutable computation; a new `chart_view` layer captures the user-editable visualization state. The same editor component works in conversation cards and dashboard tiles.

## Non-goals

- No automated test suite (manual smoke tests).
- No migration of v4 saved charts (the user re-saves after the upgrade, per the established pattern).
- No multi-user / role-based access control. Editing is gated by an Edit-mode toggle ("controlled access" per user clarification).
- No AND/OR filter builder; filters are AND-only.
- No editing of recipes (sources, ops); only the visualization layer is editable.
- No new chart types beyond the existing 10.
- No schema versioning beyond the existing corrupt-file backup.

## Architecture

**Separation of computation and visualization.**

| Layer | Owner | Editable? | Purpose |
|---|---|---|---|
| `recipe` | Agent | No | Sources + ops + optional chart spec. The unit of computation. |
| `chart_view` | User | Yes (via editor) | Title, type, axes, units, labels, filters. The unit of visualization. |
| `figure` | Renderer | No (computed) | Plotly figure built from `apply(chart_view, recipe_result_df)`. |

**Render pipeline:**

```
Recipe -> recipe_executor.execute() -> result.df + sources_used + methodology_steps
                                            |
                       chart_view (default from recipe.chart + features.json metadata)
                                            |
                    chart_view.apply(df, feature_columns) -> filtered df + AxisHints
                                            |
                                  renderer.render() -> plotly.Figure
```

**Edit flow:**

```
Click Edit on chart -> Editor expander opens (charts/chart_editor.py)
   -> user changes a control
   -> chart_view updates in session state
   -> renderer rebuilds figure live
   -> user clicks Save -> chart_view persisted in saved_charts.json
```

**Default `chart_view` derivation:**

| Origin | Defaults from |
|---|---|
| Direct chart | `recipe.chart` block + `features.json` metadata of the source feature |
| Derived analysis source chart | Feature catalog hints (`suggested_chart` / `x_field` / `y_field` / `y_fields`) + `features.json` metadata |
| Saved chart loaded from disk | Persisted `chart_view` in saved_charts.json |

**Identity / dedup:** saved-chart identity remains `recipe_hash`. Editing a saved chart's view updates the existing entry's `chart_view` in place. Saving an unsaved direct chart creates a new entry. Saving a derived analysis source chart creates a new entry whose recipe is a synthesized direct recipe over that single feature.

**Edit availability:**

| Card type | Edit | Save |
|---|---|---|
| Direct chart in conversation | yes | yes |
| Derived analysis source chart | yes | yes (creates new direct-recipe entry) |
| Saved chart in dashboard | yes | yes (updates in place) |

## `features.json` schema extension

### Per-column metadata

Each feature gains an optional `columns` object. Backwards-compatible with inference fallback for unspecified entries.

```json
{
  "feature_id": "F012",
  "feature_name": "Orders by Product Category",
  "category": "Revenue",
  "tags": ["orders", "gmv", "category"],
  "suggested_chart": "bar",
  "x_field": "category",
  "y_field": "orders",
  "columns": {
    "category":         {"label": "Category",         "kind": "dimension", "unit": "string"},
    "orders":           {"label": "Orders",           "kind": "measure",   "unit": "count"},
    "gmv_usd":          {"label": "GMV",              "kind": "measure",   "unit": "usd"},
    "avg_order_value":  {"label": "Avg Order Value",  "kind": "measure",   "unit": "usd"},
    "return_rate_pct":  {"label": "Return Rate",      "kind": "measure",   "unit": "pct"}
  },
  "data": [...]
}
```

### `kind` semantics

- `dimension`: usable for X axis or color grouping. Not allowed for Y on bar/line.
- `measure`: usable for Y. Allowed for X only on scatter.

The editor's column dropdowns filter options by `kind`.

### Locked unit set

```
usd     -> tickprefix "$", thousands separator
pct     -> ticksuffix "%"
count   -> thousands separator, no symbol
hours   -> ticksuffix "h"
days    -> ticksuffix "d"
date    -> Plotly date axis
string  -> categorical
number  -> plain numeric
```

These map directly to Plotly axis tickformat / tickprefix / ticksuffix calls. The unit set is locked - users cannot type free-form units.

### Loader behavior

`Feature` gains `columns: dict[str, ColumnMeta]`. New `ColumnMeta = {label, kind, unit}` dataclass.

Parsing rules:
1. If `columns` present, parse each. Validate: `kind` ∈ `{dimension, measure}`, `unit` in the locked set. Unknown values raise `FeaturesValidationError`.
2. If a column is missing from `columns` (or the whole `columns` block is absent), infer:
   - `kind`: numeric dtype → `measure`; otherwise `dimension`.
   - `unit`: column endswith `_usd` → `usd`; `_pct` → `pct`; `_hrs`/`_hours` → `hours`; `_days` → `days`; column name in `{date, month, quarter}` → `date`; numeric → `count`; else `string`.
   - `label`: title-case the column name (`avg_order_value` → `Avg Order Value`).

All 15 features get `columns` metadata in this implementation. The fallback exists for forward compatibility.

### Renderer behavior change

Today's `_apply_executive_theme` looks at all dataframe columns for `_usd`/`_pct` suffixes - this is the source of the wrong-`$` bug. After this change, formatting is driven entirely by per-axis unit hints from the chart_view (resolved through `column_units` overrides → `features.json` metadata → inference). The renderer never inspects column names.

## `chart_view` data model

### Dataclasses

```python
@dataclass
class ChartView:
    title: str
    type: str                           # bar | line | scatter | pie | histogram | box | heatmap | funnel | grouped_bar | horizontal_bar
    x: str
    y: list[str]                        # 1+ columns; multi-measure when len > 1
    color: str | None
    column_labels: dict[str, str]       # display name overrides
    column_units: dict[str, str]        # unit overrides
    filters: list[ChartViewFilter]

@dataclass
class ChartViewFilter:
    column: str
    op: str                             # ==, !=, <, <=, >, >=, in, between
    value: Any                          # scalar, or list for in/between

@dataclass
class AxisHints:
    x_unit: str
    x_label: str
    left_y_unit: str
    left_y_label: str
    right_y_unit: str | None
    right_y_label: str | None
```

### JSON shape

```json
{
  "title": "MRR Trend",
  "type": "line",
  "x": "month",
  "y": ["mrr_usd"],
  "color": null,
  "column_labels": {"mrr_usd": "MRR (USD)"},
  "column_units": {},
  "filters": [
    {"column": "month", "op": ">=", "value": "2024-06"}
  ]
}
```

### Default-view factory

`default_chart_view(recipe, executor_result, features)` builds the initial chart_view. Logic per origin in the Architecture section.

### Apply pipeline

`charts/chart_view.py:apply(view, df, feature_columns) -> tuple[pd.DataFrame, AxisHints]`:

1. Apply each `view.filters` entry (AND'd) to `df`. A filter referencing a missing column logs a warning and skips that filter (does not crash).
2. Resolve axis units: `column_units[col]` → `feature_columns[col].unit` → fallback `string`. The X axis uses `view.x`'s unit. The left Y uses `view.y[0]`'s unit. The right Y is set only if multi-measure with mixed units (Section: Multi-measure rendering).
3. Resolve axis labels: `column_labels[col]` → `feature_columns[col].label` → title-cased column name.
4. Return the filtered df and the `AxisHints`.

### Validation

`ChartView.from_dict` validates:
- `type` in renderer's `ALLOWED_TYPES`.
- `x` is a non-empty string.
- `y` is a non-empty list of strings.
- For single-measure-only types (`pie`, `heatmap`, `funnel`, `histogram`, `box`), `len(y) == 1`. Else fail.
- Filter ops are in the allowed set.
- Unit overrides are in the locked unit set.

Invalid views render an inline error message in the chart area instead of crashing the page. The Save button is disabled while the view is invalid.

## Multi-measure rendering

### Compatibility matrix

| Chart type | Single-Y only? | Multi-Y? | Color grouping? |
|---|---|---|---|
| line, bar, scatter | no | YES | only when single-Y |
| pie, heatmap, funnel, histogram, box | YES | no | n/a |
| grouped_bar | (uses `y_fields`, treated as multi internally) | YES | n/a |
| horizontal_bar | YES | no | yes |

The editor enforces these constraints: switching to a single-measure-only type while `len(y) > 1` truncates `y` to `y[:1]` with a warning toast.

### Axis assignment algorithm

```python
def assign_axes(y_columns: list[str], view, feature_columns) -> tuple[list[tuple[str, str]], str | None]:
    units = [resolve_unit(c, view, feature_columns) for c in y_columns]
    distinct = list(dict.fromkeys(units))  # ordered unique

    if len(distinct) == 1:
        return [(c, u) for c, u in zip(y_columns, units)], None  # single shared axis

    if len(distinct) == 2:
        left_unit, right_unit = distinct
        return [(c, u) for c, u in zip(y_columns, units)], right_unit

    raise ChartSpecError(
        f"Cannot plot {len(distinct)} distinct units on one chart: {distinct}. "
        "Pick at most 2 different units."
    )
```

Three or more distinct units → invalid view, inline error, Save disabled until the user removes a measure.

### Plotly implementation

Use `go.Figure` with explicit traces. For multi-measure on `line`/`bar`/`scatter`:
- Each measure becomes a trace.
- Series whose unit matches the right axis get `yaxis="y2"`.
- Layout includes both `yaxis` and (conditionally) `yaxis2` with `side="right", overlaying="y"`.
- Trace colors come from a small palette (`px.colors.qualitative.Set2`).
- Trace names use the column's display label.

### Color picker visibility

For multi-measure charts, the editor hides the Color picker. Each measure already gets its own trace; color grouping would create ambiguity.

## Editor UI

### Module: `charts/chart_editor.py`

```python
def render_chart_editor(
    chart_view: ChartView,
    df: pd.DataFrame,
    feature_columns: dict[str, ColumnMeta],
    key_prefix: str,
) -> ChartView:
    """Renders editor controls inside an st.expander. Returns updated chart_view."""
```

### Layout

```
[ Title text input ----------------------------------------- ]
[ Type dropdown ]                  [ X axis dropdown ------- ]

[ Y measures multi-select ------------------------------- ]
   [ Color dropdown - hidden when len(y) > 1 ]

[ Column display labels (heading) ]
  - month             [ Month                              ]
  - mrr_usd           [ MRR (USD)                          ]

[ Column units (heading) ]
  - mrr_usd           [ usd v ]
  - churn_rate_pct    [ pct v ]

[ Filters (heading) ]
  - [ column v ] [ op v ] [ value         ] [x]
  - [ column v ] [ op v ] [ value         ] [x]
  [ + Add filter ]

[ Reset to default ]                                [ Save ]
```

### Control behavior

- **Title**: `st.text_input`. Live-updates `chart_view.title`.
- **Type**: `st.selectbox` over `ALLOWED_TYPES`. Changing to a single-measure-only type while multi-measure → truncate Y, warn.
- **X axis**: `st.selectbox`. For bar/line: only `dimension` columns. For scatter: dimensions and measures.
- **Y measures**: `st.multiselect` of `measure` columns for multi-measure types; `st.selectbox` for single-measure types.
- **Color**: `st.selectbox` of dimension columns + "(none)". Hidden when `len(y) > 1`.
- **Column display labels**: `st.text_input` per column referenced in x/y. Empty = use feature metadata default.
- **Column units**: `st.selectbox` from the locked unit set, per column referenced in y. Default selection = feature metadata. Stored in `chart_view.column_units`.
- **Filters**: row layout `st.columns([2, 1, 2, 0.5])` with column / op / value / remove. Add button appends. Value input adapts to op (single text, comma-separated for `in`, two inputs for `between`).
- **Reset to default**: button. Reverts session state to `default_chart_view(...)` for that origin. Live preview updates.
- **Save**: button. Persists chart_view via `dashboard.store.save_chart` or `update_chart_view` depending on whether the chart already has a saved id.

### Live preview

The chart card always renders the figure from the current chart_view (in session state, keyed by `key_prefix`). Each control change reruns Streamlit, the chart_view updates, the figure rebuilds, the user sees the change.

### Validation feedback

Invalid view → chart area shows inline error: "Cannot plot 3 distinct units (usd, pct, count). Pick at most 2." Save button is disabled until valid.

### Y-required guard

The Y picker enforces `len(y) >= 1`. Trying to clear all selections flashes a warning and reverts to the previous valid value.

### Edit-mode toggle

Each chart card has an Edit button. `st.session_state[f"edit_open_{key_prefix}"]` controls expander visibility. When closed, the editor is hidden but the rest of the card (chart, source caption, raw data) renders normally.

## Saved-chart store

### Schema (modified)

```python
@dataclass
class SavedChart:
    id: str
    name: str           # mirrors chart_view.title; kept for back-compat with rename UI
    recipe: dict
    chart_view: dict
    created_at: str
    updated_at: str     # NEW: bumped on every chart_view edit
```

### File on disk

```json
[
  {
    "id": "uuid-1",
    "name": "MRR Trend",
    "recipe": {"sources": ["F001"], "ops": [], "chart": {...}, "stats": []},
    "chart_view": { "title": "MRR Trend", "type": "line", "x": "month", "y": ["mrr_usd"], "color": null, "column_labels": {}, "column_units": {}, "filters": [] },
    "created_at": "2026-05-01T12:00:00Z",
    "updated_at": "2026-05-01T12:00:00Z"
  }
]
```

### Save / update semantics

- `save_chart(name, recipe, chart_view, path)`: lookup by `recipe_hash`. Match → replace `chart_view`, `name`, bump `updated_at`. Miss → insert new entry. Returns the persisted `SavedChart`.
- `update_chart_view(saved_id, chart_view, path)`: find by id. Replace `chart_view`, sync `name = chart_view.title`, bump `updated_at`.
- `rename_chart(saved_id, new_name, path)`: find by id. Set `name = new_name`, sync `chart_view.title`, bump `updated_at`.
- `delete_chart(saved_id, path)`: unchanged from v4.
- `is_saved(recipe, path)`: lookup by `recipe_hash` only. Used to render the "Saved" button label.

### Dedup

`recipe_hash` is the dedup key. Two saves with the same recipe but different chart_views update the same entry. Two distinct views of the same data require two distinct recipes (e.g., add a trivial sort op). This is an accepted limitation.

### Loader

`load_saved_charts` parses each entry with `recipe` and `chart_view` both required. Missing or invalid → file treated as corrupt, backed up to `.bak`, fresh start. No migration of v4-shape entries.

### Atomic writes

Existing `_atomic_write` is unchanged.

## Components and file changes

### New files

**`charts/chart_view.py`** - `ChartView` dataclass, `ChartViewFilter`, `AxisHints`, `default_chart_view(recipe, executor_result, features) -> ChartView`, `apply(view, df, feature_columns) -> (df, hints)`, `ChartView.from_dict` / `to_dict`. ~250 lines.

**`charts/chart_editor.py`** - `render_chart_editor(chart_view, df, feature_columns, key_prefix) -> ChartView`. The Streamlit editor UI. ~250 lines.

### Modified files

**`charts/renderer.py`** - Replace `spec_to_figure(spec, data)` with `render(view, df, axis_hints)`. Add `_render_multi_measure`, `_render_single_measure`, `_render_grouped_bar`, `_render_horizontal_bar`. Add `_axis_layout(unit, label, **extra)`. Remove `_apply_executive_theme` (the `$`-on-orders bug source).

**`charts/chart_actions.py`** - Add Edit expander hosting `chart_editor.render_chart_editor`. Render figure via `render(view, df, axis_hints)` on every rerun (no cached figure). The chart_view in session state drives every render. The chart card needs the source dataframe; this is reconstructed from `chart_meta.data_columnar` at display time.

**`charts/analysis_card.py`** - Edit expander + Save button on each source chart. Save creates a new direct-recipe SavedChart for that source feature.

**`agent/tools.py`** - `ChartMeta` gains `chart_view: dict` populated from `default_chart_view(...)` at analyze time. Both `_emit_direct` and `_emit_derived` populate it. The `figure` field is REMOVED from `ChartMeta`. Figures are always rendered fresh from `(chart_view, df)` at display time - no caching. This removes the cache-invalidation problem entirely (every chart_view change triggers a Streamlit rerun, the chart card calls `render(view, df, axis_hints)`, and Plotly re-renders).

**`agent/recipe_executor.py`** - `ExecutionResult.figure` is removed. `source_figures` is removed. The executor returns `df`, `mode`, `stats`, `recipe_text`, `sources_used`, `source_dataframes`, `methodology_steps`. Figure construction moves downstream (tools.py uses `default_chart_view` + `render` to build the initial figure).

**`features/loader.py`** - `Feature` gains `columns: dict[str, ColumnMeta]`. New `ColumnMeta` dataclass. New `infer_column_meta(name, sample_value) -> ColumnMeta` for the fallback. `_parse_entry` reads the `columns` block when present.

**`dashboard/store.py`** - `SavedChart.chart_view` field. `save_chart` updates in place on hash match, inserts otherwise. New `update_chart_view(saved_id, chart_view, path)`. `rename_chart` syncs `chart_view.title`.

**`views/dashboard.py`** - Each tile renders the chart via `render(view, df, axis_hints)`. "How this was computed" expander REMOVED. Adds Source caption + Raw data expander + Edit chart expander. Save button uses `update_chart_view`.

**`views/conversation.py`** - No structural change. Chart cards manage their own editor.

**`data/features.json`** - `columns` metadata added for all 15 features.

### File map

| File | Status | Purpose |
|---|---|---|
| `charts/chart_view.py` | New | ChartView + defaults + apply pipeline |
| `charts/chart_editor.py` | New | Streamlit editor expander |
| `charts/renderer.py` | Rewritten | Unit-aware multi-measure renderer with dual-axis |
| `charts/chart_actions.py` | Changed | Edit expander, render via chart_view |
| `charts/analysis_card.py` | Changed | Edit + Save on source charts |
| `agent/tools.py` | Changed | ChartMeta.chart_view |
| `agent/recipe_executor.py` | Changed | Return df + source_dataframes; no figure |
| `features/loader.py` | Changed | columns metadata, ColumnMeta, inference |
| `dashboard/store.py` | Changed | SavedChart.chart_view, update_chart_view |
| `views/dashboard.py` | Changed | Edit + Raw data; no "how it was computed" |
| `views/conversation.py` | Unchanged | - |
| `data/features.json` | Changed | columns metadata for all 15 features |

## Manual smoke tests

### Bug fix

1. "Show me orders by category." (F012) → bar chart with counts on Y, no `$`.
2. "Plot GMV by category." → bar with `$`.
3. "Show return rate by category." → bar with `%`, no `$`.

### Editor: open / live preview / save

4. "Show me MRR over time." → click Edit. Editor opens with the right defaults.
5. Change Title → "MRR Trend Q1-Q4". Chart title updates immediately.
6. Change Type → "bar". Chart re-renders as bar.
7. Click Save. Toast confirms.
8. Switch to Dashboard. Saved chart shows as bar. No "How this was computed" expander; instead Source caption + Raw data + Edit chart.

### Multi-measure

9. "Compare MRR and Churn over time." → derived analysis card with two source charts.
10. Edit MRR source → no changes needed for direct.
11. Edit a chart with multiple measure columns ("Show me NRR" - F011). Editor's Y multi-select lists all 6 measures.
12. Pick `[starting_mrr, ending_mrr]` → two lines, single shared axis.
13. Add `nrr_pct` → dual-axis ($ left, % right).
14. Add `churned_mrr` → still 2 distinct units, dual-axis.
15. Override one measure's unit to `count` → 3 distinct units → inline error, Save disabled.
16. Remove the override → re-enabled.

### Filters

17. Edit a chart. Filter `month >= 2024-06` → chart updates.
18. Add second filter `mrr_usd > 90000` → chart shows AND result.
19. Remove second filter → chart updates.
20. Confirm Raw data expander shows ALL rows (post-execution filters do not affect raw data).

### Column labels and units

21. Override `mrr_usd` label → "Recurring Revenue". Legend / tooltip uses new label.
22. Save. Switch to Dashboard. Reopen editor. Override persists.
23. Override `mrr_usd` unit → `number`. Y-axis loses `$`.
24. Reset override → Y-axis returns to `$`.

### Reset to default

25. Make several changes. Click Reset. Editor reverts; chart re-renders to original.

### Editing derived source

26. From multi-feature derived analysis, edit Churn source chart. Type → bar. Save.
27. New SavedChart created with synthesized direct recipe. Toast confirms. Dashboard shows the new tile.

### Saved chart editing in dashboard

28. From Dashboard, edit saved Churn chart from #27. Override label for `churn_rate_pct` → "Monthly Churn %". Save.
29. Browser refresh. Open Dashboard. Edit same chart. Override persisted.

### Dedup on save

30. "Show me DAU." Save. Toast.
31. Edit Title → "DAU Tracker". Save again.
32. Dashboard shows ONE DAU tile titled "DAU Tracker", not two.

### Renaming

33. Dashboard inline rename: "MRR Trend Q1-Q4" → "Monthly Revenue". Reopen editor → Title shows the rename.

### Edge: empty Y

34. Editor: try to clear Y. Warning shown, selection reverts.

### Edge: chart_view incompatible with data

35. Hand-edit a saved chart's chart_view to reference a removed column. Open Dashboard. Tile shows inline error: "Column 'X' not found." Other tiles render. Open editor, pick valid X. Tile renders.

### Metadata fallback

36. Remove `columns` block from one feature in `data/features.json`. Reload. That feature still charts correctly via inference. Editor dropdowns still populate.
37. Restore the `columns` block.

### Feature spot-check

38. Direct chart for every feature. Verify the Y axis formatter matches the column's unit:
    - F001 mrr_usd → `$`
    - F002 churn_rate_pct → `%`
    - F003 revenue_usd → `$`
    - F005 cac_usd → `$`
    - F007 ticket_count → count
    - F008 dau → count
    - F012 orders → count, no `$` (THE BUG FIX)
    - F015 nps_score → number

### Reasoning trace + methodology

39. Confirm Reasoning trace and Methodology block from v4 are unchanged.

## Out of scope

- Multi-user / role-based permissions.
- AND/OR filter trees with nested groups.
- Editing recipes (sources, ops); only chart_view is editable.
- Schema versioning for saved charts beyond corrupt-file backup.
- Custom unit definitions beyond the locked set.
- Saving multiple distinct chart_views for the same recipe.

## Open questions

None at design-approval time.
