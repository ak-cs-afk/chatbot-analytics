# Recipe-Based Chart Engine (v3) - Design

**Date:** 2026-04-30
**Status:** Approved (pending user spec review)
**Supersedes:** parts of `2026-04-29-features-chatbot-v2-design.md` (chart pipeline, agent tools, saved-chart store)

## Purpose

Rework the chat-and-chart pipeline so that:

1. The agent can pull data points from one or more features per query and analyze them together.
2. Every chart - direct or derived - is produced by a *recipe* that is persisted with the chart, so the dashboard can re-render it from the latest features data.
3. The agent never fabricates data: when no feature matches the user's question, it refuses cleanly and lists what is available.
4. Replies are professional and explain how the numbers were calculated, citing the source features.
5. Chart names follow a strict policy: direct charts use the feature's canonical name; derived charts get an agent-authored descriptive title.

This replaces the v2 model where each chart was tied to a single `feature_id` plus a chart spec.

## Non-goals

- No new features added to `data/features.json`.
- No automated test suite. Verification is manual smoke tests.
- No migration of pre-existing saved charts. The save-store starts fresh.
- No multi-user/auth changes.
- No streaming of partial results inside a single tool call.

## Architecture

Every chart - direct or derived - is the output of a *recipe* run by a single executor. The recipe is the unit of work: the agent emits it, the executor runs it, the dashboard re-runs it on every visit. Saved charts persist the recipe, not the data.

### Two-tool agent surface

The agent's OpenAI tool list is reduced to two functions:

- `peek_feature(feature_id)` returns `{feature_id, name, columns: [{name, dtype}], sample_rows: [...3...]}`. Errors on unknown IDs.
- `analyze(recipe)` validates and executes the recipe and returns `{chart_id, name, data_preview, stats, recipe_text, sources_used}`.

The previous tools (`get_feature_data`, `make_chart`, `compute_stats`) are removed.

### Recipe shape

```json
{
  "sources": ["feature_id_1", "feature_id_2"],
  "ops": [
    {"type": "filter", "...": "..."},
    {"type": "groupby", "...": "..."},
    {"type": "join", "...": "..."},
    {"type": "derive", "...": "..."},
    {"type": "sort", "...": "..."},
    {"type": "top_n", "...": "..."},
    {"type": "time_bucket", "...": "..."},
    {"type": "custom_python", "code": "..."}
  ],
  "chart": { "type": "...", "x": "...", "y": "...", "title": "...": "..."},
  "stats": ["mean", "min", "max"]
}
```

- `sources`: 1+ feature IDs from the catalog. The first source is the initial DataFrame; later sources are pulled in via `join` ops.
- `ops`: applied in order. Empty list = no-op (this is what makes a recipe "direct").
- `chart`: optional. Same shape as today's chart spec consumed by `charts/renderer.py`. Omitted = stats-only response.
- `stats`: optional list of aggregations to compute over the final DataFrame's numeric columns.

### Direct vs derived classification

Auto-classified by the executor:

- `len(sources) == 1` AND `ops == []` → "direct chart". Executor overrides any agent-supplied `chart.title` with the feature's `feature_name` from the catalog.
- Otherwise → "derived chart". Executor preserves the agent-authored `chart.title`.

This is the enforcement point for the naming policy.

### Re-render guarantee

The dashboard reloads `features.json`, finds each saved recipe's sources, re-runs the ops on the latest data, and re-builds the figure. No data is cached in the saved-chart file.

## Components

### `agent/recipe.py` (new)

Pure data layer. Defines `Recipe`, `Op`, and op subtypes (`FilterOp`, `GroupbyOp`, `JoinOp`, `DeriveOp`, `SortOp`, `TopNOp`, `TimeBucketOp`, `CustomPythonOp`) as dataclasses. Includes `Recipe.from_dict` / `to_dict` for JSON round-trip and a validator that checks: sources are non-empty, every op has required fields, chart spec (if present) references columns the ops are expected to produce, `derive.expr` is safe, `custom_python.code` is under the length cap. No execution, no pandas. Target ~150 lines.

### `agent/recipe_executor.py` (new)

The runtime. Single entry: `execute(recipe: Recipe, features: dict[str, Feature]) -> ExecutionResult` where `ExecutionResult = {df, stats, figure, recipe_text, sources_used}`. Loads each source feature into a DataFrame, applies ops in order, then builds a Plotly figure via the existing `charts/renderer.py:spec_to_figure` and computes requested stats. Generates a human-readable `recipe_text` (e.g., "Joined MRR + Churn on month, computed ratio = mrr/churn, kept top 12 months by ratio."). Auto-classifies direct vs derived and applies the title-override rule.

### `agent/sandbox.py` (new)

Restricted exec for the `custom_python` op. Provides `run_user_code(code: str, df_in: pd.DataFrame) -> pd.DataFrame` with:

- Stripped builtins (only `len`, `range`, `sum`, `min`, `max`, `abs`, `round`, `sorted`, `enumerate`, `zip`, `dict`, `list`, `tuple`, `set`, `str`, `int`, `float`, `bool`).
- Injected `pd` and `np`.
- No `import` (rejected at parse time via AST scan).
- No file or network access.
- Best-effort 5-second wall timeout via `threading.Timer` + a polled interrupt flag (Windows-friendly; documented as best-effort, not a security boundary).
- Code length capped at 2000 chars.

### `agent/tools.py` (rewrite)

Two functions surfaced to OpenAI: `peek_feature` and `analyze`. `analyze` calls into `recipe_executor.execute`, packages the result into the existing `ChartMeta` shape but with the recipe attached, and returns a compact JSON to the LLM (data preview = first 5 rows, stats dict, recipe_text, chart_id, sources_used). The figure object stays in process state; only its id is returned to the LLM.

`ChartMeta` dataclass changes: gains a `recipe` field; the legacy `feature_id` and `spec` fields are removed.

### `agent/prompts.py` (rewrite)

System prompt is rewritten end-to-end:

- Catalog of features with feature_id, name, description, columns.
- Strict refusal rule with exact wording template.
- Naming policy: direct charts use `feature_name`, derived charts get an agent-authored title.
- Methodology rule: include a one-sentence "Computed by …" line in the assistant's text reply.
- Recipe authoring guidance with 2-3 worked examples (direct, derived single-source, derived multi-source).
- Tool-use loop: peek_feature first when in doubt about schema, then analyze.
- No-fabrication rule: cite numbers only from `data_preview` or `stats`. If a value isn't in the response, run another `analyze` or omit the claim.

### `charts/chart_actions.py` (changed)

Add a `st.expander("How this was calculated")` below the chart that renders: bullet list of source features (id + name), the `recipe_text`, and the raw recipe JSON in a code block. Closed by default.

### `views/conversation.py` (changed)

Reads the methodology line off the assistant turn and shows it inline. Charts list rendering is otherwise the same; chart cards now include the expander automatically because `chart_actions.py` always shows it. Saved-keys set in session state switches from `(feature_id, spec_hash)` tuples to plain `recipe_hash` strings.

### `views/dashboard.py` (changed)

Uses `recipe_executor.execute` to rebuild every saved chart from the latest `features.json` data on each visit. Failure for one chart shows a placeholder card ("Could not refresh: <error>", with a small "View saved recipe" expander) and continues with the others. Saved entry is NOT deleted automatically on failure.

### `dashboard/store.py` (changed)

`SavedChart = {id, name, recipe, created_at}`. The `_spec_hash` helper is replaced by `_recipe_hash(recipe_dict)` (canonical JSON via `json.dumps(..., sort_keys=True, separators=(",", ":"))`, then SHA-256 hex digest). `save_chart` signature changes from `(name, feature_id, spec, path)` to `(name, recipe, path)`. No legacy field handling in the loader.

## Recipe DSL specification

The recipe is a JSON object. Every op consumes the working DataFrame from the previous step and produces the next.

### Op catalog (initial set)

| Op | Required fields | Behavior |
|---|---|---|
| `filter` | `column`, `op` (`==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `between`), `value` | Row filter. |
| `groupby` | `by` (list of columns), `agg` (dict of `column: aggfn`) | Group + aggregate. `aggfn` ∈ `sum`, `mean`, `median`, `min`, `max`, `count`, `nunique`. |
| `join` | `with` (feature_id), `on` (column or list), `how` (`inner`/`left`, default `inner`) | Pulls in another source and merges on the named column(s). |
| `derive` | `name` (new column), `expr` (string, restricted) | Adds a computed column. `expr` runs through a safe evaluator that allows `+ - * / // % ** ()`, column names, numeric literals, and a fixed set of functions: `abs`, `min`, `max`, `round`, `log`, `sqrt`. No attribute access, no `__`. |
| `sort` | `by` (column), `order` (`asc`/`desc`, default `asc`) | Sort. |
| `top_n` | `n` (int), `by` (column) | Sort desc + head(n). |
| `time_bucket` | `column`, `freq` (`D`/`W`/`M`/`Q`/`Y`) | Parses the column as datetime and replaces its values with the start of the corresponding period (e.g., `freq=M` rounds every date down to the first of its month). Does NOT aggregate by itself; combine with a subsequent `groupby` to roll up. |
| `custom_python` | `code` (string, ≤2000 chars) | Escape hatch. Receives `df` and `pd`/`np`; must return a DataFrame. Sandboxed per `agent/sandbox.py`. |

### Validation (in `Recipe.from_dict`)

- Unknown op type → reject.
- Required fields missing → reject with field name.
- `chart.type` not in renderer's `ALLOWED_TYPES` → reject.
- `derive.expr` containing `__` or `import` or attribute access → reject.
- `custom_python.code` over 2000 chars → reject.
- AST scan of `custom_python.code` finds an `Import`/`ImportFrom` node → reject.

### Worked examples

**Direct chart:**

```json
{
  "sources": ["dau"],
  "ops": [],
  "chart": {"type": "line", "x": "date", "y": "dau"}
}
```

Title omitted; executor uses the feature's `feature_name` as the chart name.

**Derived single-source - top N:**

```json
{
  "sources": ["cac_by_channel"],
  "ops": [
    {"type": "top_n", "n": 5, "by": "cac_usd"}
  ],
  "chart": {"type": "horizontal_bar", "x": "cac_usd", "y": "channel", "title": "Top 5 Channels by CAC"}
}
```

**Derived multi-source - join + derive:**

```json
{
  "sources": ["mrr", "churn_rate"],
  "ops": [
    {"type": "join", "with": "churn_rate", "on": "month"},
    {"type": "derive", "name": "net_growth_pct", "expr": "(mrr_change_pct - churn_rate_pct)"},
    {"type": "sort", "by": "month"}
  ],
  "chart": {"type": "line", "x": "month", "y": "net_growth_pct", "title": "Net Growth: MRR Change Minus Churn"},
  "stats": ["mean", "min", "max"]
}
```

**Stats-only (no chart):**

```json
{
  "sources": ["nps"],
  "ops": [],
  "stats": ["mean", "min", "max"]
}
```

## Data flow

### Conversation turn (new chart produced)

1. User asks a question.
2. **Agent loop iteration 1:** model has the catalog in the system prompt, decides which feature(s) it needs. Calls `peek_feature(id)` for each to confirm column names + dtypes from sample rows.
3. **Agent loop iteration 2:** model writes a recipe and calls `analyze(recipe)`.
4. **`analyze` server-side:**
   - Validates the recipe via `Recipe.from_dict`.
   - Calls `recipe_executor.execute(recipe, features)`.
   - Executor: loads each source as DataFrame, applies ops in order, builds figure via `spec_to_figure`, computes stats, generates `recipe_text`.
   - Auto-classifies direct vs derived. If direct, sets `chart.title = feature.feature_name`.
   - Builds a `ChartMeta(chart_id=uuid, name=title, recipe=recipe, figure=fig)` and stashes the figure in turn-local state.
   - Returns to LLM: `{chart_id, name, data_preview: first_5_rows, stats, recipe_text, sources_used: [{id, name}]}`.
5. **Agent loop iteration 3:** model writes the assistant text reply with the required structure (enforced by prompt):
   - One-sentence methodology line.
   - Numeric findings citing only the values from `data_preview` / `stats`.
   - Optional plain-English summary.
6. `AnalyticsAgent.run_streaming` finalizes an `AssistantTurn(text, charts=[ChartMeta], error=None)`.
7. `views/conversation.py` renders: assistant markdown text → `_render_chart_list(charts)`. Each chart card shows: editable name, Plotly figure, "How this was calculated" expander (sources + recipe_text + JSON), Save button.
8. **User clicks Save:** `_on_save(cm)` calls `dashboard.store.save_chart(name, recipe)`. Writes to `data/saved_charts.json`. Saved-keys set updated by hashing the recipe.

### Dashboard render

1. User opens Dashboard tab.
2. `views/dashboard.py` calls `load_saved_charts(SAVED_CHARTS_PATH)` → `list[SavedChart]`.
3. `load_features()` (cached) gives latest features.
4. For each `SavedChart`: try `recipe_executor.execute(saved.recipe, features)`. On success, render `chart_actions.render_chart_with_actions` with the fresh figure. On failure, render a placeholder card with the error message and the saved name.
5. User-editable name updates write back through `store.rename`. Delete button removes the entry. Both unchanged from today, just operating on the new schema.

### Refusal turn (no matching feature)

1. User asks for a metric not in the catalog.
2. Model scans catalog, finds no match. Per system prompt, replies in text only - no tool calls - with the locked refusal template:

> "I don't have data on **{topic}**. Available metrics: {comma-separated list of feature_names}. Want to ask about one of those?"

3. `AnalyticsAgent` returns `AssistantTurn(text=…, charts=[], error=None)`. UI shows just the text.

## Error handling

| Failure | Where | Visible to | Handling |
|---|---|---|---|
| Agent calls `peek_feature("does_not_exist")` | `tools.peek_feature` | LLM (tool error) | Returns `{error: "feature_id 'X' not found. Available: [...]"}`. LLM is expected to refuse cleanly to the user. |
| Agent submits invalid recipe | `Recipe.from_dict` | LLM (tool error) | `analyze` returns `{error: "Recipe validation failed: <reason>"}`. LLM either retries with a corrected recipe or apologizes. |
| Recipe references a column that doesn't exist after prior ops | `recipe_executor.execute` | LLM (tool error) | Returns `{error: "Op 'derive': column 'X' not found. Available after step 2: [...]"}`. LLM retries. |
| `custom_python` raises, times out, or returns non-DataFrame | `agent/sandbox.py` | LLM (tool error) | Returns `{error: "custom_python: <type>: <msg>"}`. LLM retries or falls back to declarative ops. |
| Chart spec invalid for the produced DataFrame | existing `ChartSpecError` | LLM (tool error) | Returns `{error: "Chart spec invalid: <reason>"}`. LLM retries with a corrected chart block. |
| Azure OpenAI HTTP/timeout error | `AnalyticsAgent.run_streaming` | User | Existing handling in `views/conversation.py` shows `st.error(...)`. Unchanged. |
| Tool-call loop exceeds 8 iterations | `AnalyticsAgent` | User | Existing limit. Returns `AssistantTurn(text="Sorry, I couldn't complete this request after several attempts.", error=True)`. Unchanged. |
| User asks for metric not in catalog | LLM | User | Plain-text refusal per system prompt; no tool calls. |
| Saved chart fails to re-render in dashboard | `views/dashboard.py` | User | Card shows: title (saved name), red text "Could not refresh: <error>", and a "View saved recipe" expander. Other cards keep rendering. Saved entry is NOT deleted automatically. |
| `data/saved_charts.json` corrupt | `dashboard.store.load_saved_charts` | User (toast) | Existing behavior: back up to `.bak` and start fresh. Unchanged. |
| `data/features.json` missing or invalid | `app._check_features` | User | Existing error screen at app start. Unchanged. |

### No-fabrication enforcement

The agent's text reply is required (by prompt) to cite numeric values that came back in the `analyze` tool response (in `data_preview` or `stats`). If it wants to mention a number not in the response, it must either run another `analyze` call or omit the claim. Programmatic enforcement is out of scope; smoke tests cover compliance.

### LLM-tool retry budget

Same 8-iteration loop as today. A single bad recipe doesn't kill the turn; the LLM gets the structured error and corrects.

## No migration

- The old `data/saved_charts.json` (if any exists) is deleted as part of the upgrade. The file is recreated fresh in the new shape on the first save. Pre-existing saved charts are gone; user re-saves what they want.
- `SavedChart` dataclass is rewritten to `{id, name, recipe, created_at}`. No legacy field handling, no fallback branches in the loader. A stray legacy entry causes the loader to treat the file as corrupt (existing `.bak` behavior kicks in).
- Tool surface is `[peek_feature, analyze]`. Old tools (`get_feature_data`, `make_chart`, `compute_stats`) are deleted outright in the same change set.
- `ChartMeta` gets `recipe` field; old `feature_id` and `spec` fields are removed (not kept alongside). In-memory chats reset on app restart.
- `_spec_hash` is deleted; callers updated to use `_recipe_hash`. Saved-keys set in `conversation.py` switches to plain `recipe_hash` strings.
- `data/features.json` schema is unchanged.

## Manual smoke tests

### Direct charts (single feature, no ops)

1. "Show me MRR over time." → line chart titled "Monthly Recurring Revenue" (canonical, NOT agent-authored). Methodology line: "Direct view of MRR." Expander shows source = MRR, ops = none.
2. "Plot DAU." → same shape, title = "Daily Active Users".

### Derived charts (single feature with ops)

3. "Show me the top 5 channels by CAC." → bar/horizontal_bar with agent-authored title (e.g., "Top 5 Channels by CAC"). Recipe: `top_n` on `cac_by_channel`.
4. "What's the average and max DAU?" → stats-only, no chart unless agent decides one is useful. Numbers in reply must come from the `analyze` response.

### Derived charts (multi-feature)

5. "Compare MRR growth and churn rate over time." → recipe joins MRR + Churn Rate on month, possibly derives a delta. Agent-authored title. Methodology line lists both sources. Expander shows the join + derive ops.
6. "What's our CAC per DAU by channel?" → join CAC by Channel + DAU, derive ratio. Agent-authored title.

### Refusal

7. "Show me employee headcount." → text reply only, follows the locked refusal template, lists available metrics. No charts.
8. "What's our gross margin?" → same: refusal, no fabrication.

### No-fabrication check

9. "What was MRR in March 2024?" → reply cites only a value present in `data_preview` rows or stats. If not present, the agent calls `analyze` again with a filter or says it can't pinpoint it.

### Save + dashboard

10. From turn 5, click "Save to Dashboard". Switch to Dashboard tab. Verify the chart re-renders from latest data with correct title and the same expander content.
11. In Dashboard, rename the chart. Reload the app. Verify the new name persists.
12. Edit `data/features.json` (e.g., append a new month to MRR). Reload features via the sidebar. Switch back to Dashboard. Verify the saved chart reflects the new data point.

### Custom-python escape hatch

13. Ask a question the declarative ops can't express cleanly (e.g., "Show me a 3-month rolling average of MRR"). Verify the agent uses `custom_python` and the chart renders. If it stays declarative, that is also fine - the test confirms the path works when needed.

### Sandbox safety spot-check

14. Manually craft a recipe with `custom_python.code = "import os; os.system('echo bad')"` and feed it to `analyze` via a quick REPL or test harness. Verify it is rejected by the import scrubbing.

### Error surfaces

15. Manually craft an invalid recipe (unknown op type) and submit via REPL → expect structured error.
16. Delete a feature_id from `data/features.json` that a saved chart depends on, reload features, open Dashboard → expect that one card shows "Could not refresh: feature_id 'X' not found" and other cards still render.

## Out of scope

- Programmatic enforcement of the no-fabrication rule (compliance is via prompt + smoke tests).
- Sandbox as a security boundary against adversarial code (best-effort only; the sandbox is for accidental misbehavior, not malicious input).
- Caching of executor results (always fresh re-render).
- Streaming partial results within a single tool call.
- Multi-user state.
- Migration of pre-existing saved charts.

## Open questions

None at design-approval time. Implementation plan will surface any remaining ambiguities.
