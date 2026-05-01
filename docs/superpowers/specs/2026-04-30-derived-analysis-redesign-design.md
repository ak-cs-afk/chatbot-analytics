# Derived-Analysis Redesign (v4) - Design

**Date:** 2026-04-30
**Status:** Approved (pending user spec review)
**Supersedes:** parts of `2026-04-30-recipe-based-chart-engine-design.md` (chart pipeline for derived recipes, conversation reply structure, app.py navigation)

## Purpose

Three problems surfaced after v3 shipped:

1. **Derived analyses produced misleading charts.** The "key metrics this period" survey showed a single `month vs mrr` chart that did not represent the multi-feature answer.
2. **The user wants derived analysis to be text-first**: detailed explanation, visible reasoning steps, and the source features rendered as their own canonical charts and raw data tables. No synthesized chart for the derivation itself.
3. **Tab-based navigation forced a CSS hack** to keep the chat input docked at the bottom. We want to remove the hack by moving navigation to the sidebar so the conversation view is at the top level of the page.

This redesign separates three response modes (direct, derived, survey) and gives each a tailored layout. Charts are reserved for direct, single-feature canonical views. Derived analysis is text-first with source feature charts + raw data alongside.

## Non-goals

- No automated test suite. Verification is manual smoke tests.
- No saving of derived analyses to the dashboard yet (architecture leaves a hook).
- No changes to the recipe DSL itself (filter/groupby/join/derive/sort/top_n/time_bucket/custom_python all stay).
- No changes to the features.json schema.
- No multi-user/auth changes.

## Architecture

Three response modes the agent can produce per turn:

| Mode | Triggered when | Output |
|---|---|---|
| **Direct chart** | Recipe has 1 source AND 0 ops | One chart card with the feature's canonical chart, `chart.title` set to the feature's `feature_name`, "How this was calculated" expander (collapsed), Save button visible. |
| **Derived analysis** | Recipe has 2+ sources OR any ops | An *analysis block* (insight prose + Note + visible Methodology bullets) followed by one canonical chart per source feature (no Save buttons on these auto-rendered charts). Each source chart has a "Raw data" expander with a sortable, scrollable `st.dataframe` (height capped at 300px) and a "Download CSV" button. |
| **Survey / mixed** | Agent calls multiple direct `analyze`s in one turn | Top-level prose summary + 2-up grid of direct chart cards (each savable). No analysis block. |

The agent decides the mode by what it calls. No `analyze_or_survey` flag - the recipe's shape determines the mode.

Behavioral changes from v3:

1. The `analyze` tool stops producing a synthesized chart for derived recipes. Even if the agent passes a `chart` block in a derived recipe, the executor ignores it. Canonical source charts are auto-built from each source feature's catalog hints (`suggested_chart` / `x_field` / `y_field` / `y_fields`).
2. `analyze` returns a different payload shape per mode (`mode: "direct"` vs `mode: "derived"`).
3. Saved-to-dashboard is gated on `mode == "direct"` only. Derived analysis cards and their source charts have no Save button. An `AnalysisCard.savable` flag is reserved for later expansion.
4. `app.py` drops `st.tabs` and the entire CSS injection. Sidebar gains a `View` radio at the top. Conversation view renders at the top level so `st.chat_input` docks naturally.

The recipe still drives every analysis. What changes is what the executor and UI *do* with the result for derived recipes.

## Tool surface

### `peek_feature` (unchanged)

Returns `{feature_id, name, description, columns, sample_rows, row_count}`. Errors on unknown IDs.

### `analyze` (mode-aware response)

Recipe input is the same JSON DSL as v3. Direct recipes still require `chart`. **Derived recipes ignore any `chart` block the agent provides** - source charts are auto-built. The prompt tells the agent not to author chart specs for derived recipes.

**Direct response shape (unchanged):**
```json
{
  "ok": true,
  "mode": "direct",
  "chart_id": 0,
  "name": "Monthly Recurring Revenue",
  "data_preview": [...5 rows...],
  "stats": {...},
  "recipe_text": "Direct view of Monthly Recurring Revenue.",
  "sources_used": [{"id": "F001", "name": "Monthly Recurring Revenue"}]
}
```

**Derived response shape (new):**
```json
{
  "ok": true,
  "mode": "derived",
  "analysis_id": 0,
  "data_preview": [...5 rows of the FINAL derived dataframe...],
  "stats": {...stats over the derived dataframe...},
  "recipe_text": "Joined MRR + Churn on month, derived net_growth_pct = mrr_change_pct - churn_rate_pct.",
  "sources_used": [
    {"id": "F001", "name": "Monthly Recurring Revenue"},
    {"id": "F002", "name": "Churn Rate"}
  ],
  "source_charts": [
    {"feature_id": "F001", "chart_id": 0, "name": "Monthly Recurring Revenue"},
    {"feature_id": "F002", "chart_id": 1, "name": "Churn Rate"}
  ],
  "methodology_steps": [
    {"step": 1, "text": "Joined MRR with Churn Rate on `month` (inner join, 12 rows matched)."},
    {"step": 2, "text": "Derived `net_growth_pct = mrr_change_pct - churn_rate_pct`."},
    {"step": 3, "text": "Computed mean and range across the period."}
  ]
}
```

### `methodology_steps` generation

The executor generates the structured steps deterministically from the recipe + execution traces (row counts after filter/join, columns added by derive, aggregation results). The model does NOT write these steps - so they cannot be hallucinated.

Steps include:
- Op type and what it did, in plain language.
- Concrete details: row count post-filter, column names introduced by derive, the aggregation result, etc.

### Data shapes

**`ChartMeta`** (modified) gains:
- `mode: Literal["direct", "derived_source"]`
- `data_columnar: dict | None` - raw feature data attached for `derived_source` charts so the UI can render the "Raw data" expander without re-fetching. None for direct charts.

**`AnalysisCard`** (new):
```python
@dataclass
class AnalysisCard:
    analysis_id: int
    sources_used: list[dict]
    methodology_steps: list[dict]
    recipe: dict
    recipe_text: str
    data_preview: list[dict]
    stats: dict
    source_chart_ids: list[int]
    savable: bool = False  # extensibility hook
```

**`AssistantTurn`** gains `analysis_cards: list[AnalysisCard]` alongside `charts`.

## Reply structure

### Direct chart reply

```
[Reasoning trace expander, collapsed]
[Assistant prose: 2-5 sentences of business insight, ending with "Direct view of {feature_name}."]
[Chart card]
  Editable name (canonical feature name)
  Plotly chart
  "How this was calculated" expander (collapsed)
  Save to Dashboard button
```

### Derived analysis reply

```
[Reasoning trace expander, collapsed]
[Assistant prose: 1-2 paragraphs of business insight + optional > Note blockquote]
[Analysis block - ALWAYS VISIBLE]
  "Methodology" heading
  Sources line: "Sources: MRR (F001), Churn Rate (F002)"
  Numbered methodology steps (auto-generated):
    1. Joined MRR with Churn Rate on `month` (inner join, 12 rows matched).
    2. Derived `net_growth_pct = mrr_change_pct - churn_rate_pct`.
    3. Computed mean and range across the period.
  Result line: "Result: avg net_growth_pct = 4.2%, range -1.8% to 9.6%"
  "View recipe (technical)" expander (collapsed) with raw recipe JSON
[Source charts grid - 2-up, no Save buttons]
  [F001 canonical chart]                  [F002 canonical chart]
  Plotly chart                            Plotly chart
  "Raw data" expander                      "Raw data" expander
    Sortable st.dataframe (h=300)           Sortable st.dataframe (h=300)
    Download CSV button                     Download CSV button
```

### Survey reply

```
[Reasoning trace expander, collapsed]
[Assistant prose: 2-3 sentence overview]
[Chart cards in 2-up grid]
  [Chart 1]   [Chart 2]
  [Chart 3]   [Chart 4]
  [Chart 5]
```

Each chart card has its full structure (editable name, expander, Save button).

### Prose length rules

| Mode | Insight length | Methodology source |
|---|---|---|
| Direct | 2-5 sentences | Single line in prose: "Direct view of {feature_name}." |
| Derived | 1-2 paragraphs (4-8 sentences) | Auto-generated `methodology_steps`; rendered as visible bullets, NOT in prose |
| Survey | 2-3 sentences total | None (each chart card has its own collapsed expander) |

For derived, the agent's prose stops at insight + optional Note. The "Computed by …" line is NOT included - that role is taken by the visible Methodology block.

### Chat history rendering

`ChartMeta` and `AnalysisCard` both persist in `st.session_state.messages` so reload replays everything. The history loop renders analysis cards (with their bundled source charts) before any standalone direct charts.

### Saving

- Direct chart: Save button visible. Click → recipe persisted to `data/saved_charts.json`.
- Derived analysis: no Save button on the analysis card or its source charts.
- Survey: each direct chart in the grid is individually savable.

The `AnalysisCard.savable: bool` flag is reserved for later expansion. Flipping it to True surfaces a Save button on the analysis block; the persistence layer is unchanged (saving an AnalysisCard means persisting its recipe to the same `saved_charts.json` with mode metadata).

## Components and file changes

### New files

**`charts/analysis_card.py`** - Renders the analysis block + source charts + raw-data expanders. Single public function `render_analysis_card(card, source_charts, message_index)`. Internal helper `render_source_chart(chart_meta, message_index)` shows a chart + "Raw data" expander with `st.dataframe(height=300)` + Download CSV button. No Save button on these.

**`charts/source_data.py`** - Tiny helper: `dataframe_to_csv_bytes(df) -> bytes` for download buttons.

### Modified files

**`agent/recipe_executor.py`**
- `ExecutionResult` gains `mode: str`, `source_dataframes: dict[str, pd.DataFrame]`, `methodology_steps: list[dict]`.
- `execute()` branches on direct vs derived. Direct: build the chart, return as today. Derived: build no synthetic chart; build canonical charts for each source from `feature.suggested_chart` / `x_field` / `y_field`; compute stats over final derived dataframe; generate `methodology_steps` from recipe + execution traces.
- New helper `_build_canonical_chart(feature)` using `spec_to_figure` with catalog hints.
- New helper `_generate_methodology_steps(recipe, execution_trace)` producing the structured steps list. Records row count after each op as it goes.

**`agent/tools.py`**
- `ChartMeta` gains `mode` and `data_columnar`.
- New `AnalysisCard` dataclass.
- `analyze()` branches on `result.mode`:
  - `"direct"` → appends one `ChartMeta(mode="direct")` to `turn.charts`. Returns existing direct payload.
  - `"derived"` → appends one `ChartMeta(mode="derived_source", data_columnar=...)` per source feature. Builds an `AnalysisCard` and appends to `turn.analysis_cards`. Returns derived payload (no synthesized `chart_id`).

**`agent/client.py`**
- `AssistantTurn` gains `analysis_cards: list[AnalysisCard]`.
- `_make_step` updated to handle the new derived-mode response shape (show sources_used count + methodology step count instead of a single chart_id).

**`agent/prompts.py`**
- Naming-policy section: clarify `chart` block in derived recipes is ignored.
- Response format section: split into Direct / Derived / Survey blocks with explicit prose-length targets and structure rules.
- Direct prose ends with "Direct view of …"; derived prose ends after insight (+ optional Note); methodology is auto-rendered.
- Add a worked example for survey ("key metrics this period") showing 4-5 direct `analyze` calls in one turn.

**`charts/chart_actions.py`**
- `render_chart_with_actions` now branches on `chart_meta.mode`. Direct → existing behavior. Source-mode rendering moves to `analysis_card.py`; this file no longer handles them.

**`views/conversation.py`**
- Render loop reorganized: prose → analysis cards (with bundled source charts via `render_analysis_card`) → standalone direct charts via `render_chart_with_actions`.
- Session state schema: each message gains `analysis_cards: list[AnalysisCard]`.

**`views/dashboard.py`** - No structural change. Saved charts are direct-only by definition.

**`app.py`**
- Drop `st.tabs`. Drop the entire CSS injection block.
- Sidebar gains `st.radio("View", ["Conversation", "Dashboard"], key="active_view")` at the top, above existing settings.
- Body: single conditional render at top level based on `st.session_state.active_view`.

**`dashboard/store.py`** - No changes.

### File map summary

| File | Status | Purpose |
|---|---|---|
| `charts/analysis_card.py` | New | Renders analysis block + source charts + raw data |
| `charts/source_data.py` | New | CSV download helper |
| `agent/recipe_executor.py` | Changed | Mode classification, source charts, methodology_steps |
| `agent/tools.py` | Changed | New ChartMeta.mode, new AnalysisCard, analyze branches |
| `agent/client.py` | Changed | AssistantTurn.analysis_cards, _make_step |
| `agent/prompts.py` | Changed | Three response modes |
| `charts/chart_actions.py` | Changed | mode-aware (only handles direct now) |
| `views/conversation.py` | Changed | New render loop with analysis cards |
| `views/dashboard.py` | Unchanged | - |
| `dashboard/store.py` | Unchanged | - |
| `app.py` | Changed | Sidebar nav, no tabs, no CSS hack |

## Navigation refactor

### New `app.py` shape

```python
def main() -> None:
    st.set_page_config(page_title="Chatbot Analytics", page_icon=":bar_chart:", layout="wide")
    st.title("Chatbot Analytics")
    st.caption("Chat with your business metrics. Save charts to a persistent dashboard.")

    if not _check_config():
        return
    if not _check_features():
        return

    _render_sidebar()

    active = st.session_state.get("active_view", "Conversation")
    if active == "Conversation":
        conversation.render()
    else:
        dashboard.render()
```

The CSS injection block is deleted entirely. Body padding reverts to Streamlit defaults.

### Sidebar order

```
Sidebar
├─ View                    [radio: Conversation / Dashboard]
├─ ─── divider ───
├─ Settings (heading)
├─ Deployment              [text input, disabled]
├─ Reload data             [button]
├─ Clear chat              [button]
├─ ─── divider ───
└─ v3 - recipe-based       [caption]
```

`st.radio` manages selection state via the `active_view` key.

### Why a radio (not buttons or selectbox)

- **Radio**: both options always visible, single click to switch, state managed by Streamlit. Best for a 2-option always-visible nav.
- **Buttons**: would require manual session-state handling and would lack the "currently selected" visual.
- **Selectbox**: collapsed by default; extra click for a binary choice.

### Why this fixes the chat input

`st.chat_input` has built-in sticky-bottom behavior **only when at the top level of the page**. Inside `st.tabs` it loses that behavior. By rendering the conversation view at the top level (under a sidebar nav), docking just works - no CSS, no media queries.

### Mobile / narrow viewports

Streamlit's sidebar collapses on narrow viewports. The radio remains accessible via the hamburger. The chat input still docks at the bottom because it is at the top level. Strictly better than the tabs+CSS approach which had to special-case `var(--sidebar-width)`.

## Manual smoke tests

Each item is one chat turn unless noted.

### Navigation

1. Launch the app. Sidebar shows "View" radio at top, "Conversation" selected. Chat input docks at the bottom of the viewport. No white strip in dark mode.
2. Switch radio to "Dashboard". Body switches. Switch back. History intact.

### Direct chart mode

3. "Show me MRR over time."
   - One chart card, title = canonical feature name.
   - Card has: editable name, plotly chart, "How this was calculated" expander (collapsed), Save button.
   - Prose 2-5 sentences ending with "Direct view of …".
4. "Plot DAU." → same shape with DAU.

### Derived analysis mode (single source with ops)

5. "Top 5 channels by CAC."
   - Prose: 1-2 paragraphs.
   - Visible Methodology block with sources line + numbered steps describing `top_n`.
   - One canonical CAC by Channel chart below (no Save button).
   - "Raw data" expander on the source chart shows `st.dataframe` + Download CSV.

### Derived analysis mode (multi-source)

6. "Compare MRR growth and churn rate over time."
   - Insight paragraph(s).
   - Methodology block lists both sources, join op (with row count), derive op, result.
   - Two canonical source charts in 2-up grid (MRR, Churn Rate). Neither savable.
   - Raw-data expander on each.
7. "How does ARPU compare with new customer acquisition over time?" → same shape with two different sources.

### Survey mode

8. "What are our key metrics this period?"
   - Prose: 2-3 sentence overview.
   - 4-5 direct chart cards in 2-up grid, each savable.
   - No analysis card.
9. "Give me an executive overview of the SaaS metrics." → same shape.

### Refusal

10. "Show me employee headcount." → text only, refusal template wording, no charts.
11. Mismatched-granularity: "What's the average support tickets per month?" → does NOT refuse. Produces a derived analysis (or direct chart of tickets-by-category) WITH a `> Note:` blockquote on its own line.

### Saving

12. From turn 3, click Save. Toast appears. Switch view to Dashboard via sidebar. Saved chart re-renders.
13. From turn 8, click Save on one card. Switch to Dashboard. Two charts saved.
14. Confirm derived analysis (turn 6) has NO Save button on the analysis block AND no Save button on either source chart.

### Recipe re-render after data change

15. Stop app. Append a new month to MRR in `data/features.json`. Restart, click Reload data. Open Dashboard. Saved MRR chart reflects the new data point.

### Reasoning trace

16. Expand "Reasoning trace" above prose for turn 6.
    - For derived: peek_feature calls + analyze call with sources + ops listed (not empty).
    - For survey: multiple analyze calls, one per feature.

### Methodology accuracy

17. After turn 6, scrutinize Methodology bullets. Every number (row counts, mean, range) must match what the executor produced. Cross-check via "View recipe (technical)" expander.
18. Prose insight cites only numbers visible in `data_preview` or `stats`. No fabricated values.

### Custom-python escape hatch

19. "Show me a 3-month rolling average of MRR."
    - Should produce a derived analysis. Methodology block shows the `custom_python` op (e.g., "Custom transformation (62 chars of Python).").
    - One canonical MRR source chart below.
    - If the agent solves it without `custom_python`, that is also acceptable.

### Sandbox safety (REPL)

20. Stop app. Invoke `analyze` with a recipe containing `{"type": "custom_python", "code": "import os"}`. Expect `RecipeValidationError: custom_python.code must not import modules.`

### Error surface in dashboard

21. Stop app. Remove a saved chart's source feature from `data/features.json`. Restart, click Reload data, open Dashboard. That tile shows "Could not refresh: …" red text. Other tiles render. Restore the file.

### Chat input docking (regression check)

22. With 10+ messages, confirm the chat input stays docked at the bottom, transparent backdrop, no white strip in dark mode, no overlap with sidebar.

## Out of scope

- Saving derived analyses to the dashboard (architecture leaves a hook).
- LLM-regenerated insight prose on dashboard re-render.
- Forecasting, anomaly detection, ML transforms (still requires `custom_python`).
- Caching of executor results (always fresh re-render).
- Multi-user state.

## Open questions

None at design-approval time.
