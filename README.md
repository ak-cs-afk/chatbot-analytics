# Chatbot Analytics 

A two-view conversational analytics app for a SaaS business. Chat with a fixed catalog of 15 pre-computed metrics (MRR, churn, ARPU, NRR, NPS, sales funnel, feature adoption, and more). Save charts to a persistent dashboard that always reflects the latest data.

## Setup

1. **Python 3.11+** required.

2. **Install dependencies:**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Configure Azure OpenAI** (must support function calling):

   ```powershell
   Copy-Item .env.example .env
   ```

   Edit `.env`:

   - `AZURE_OPENAI_ENDPOINT`
   - `AZURE_OPENAI_API_KEY`
   - `AZURE_OPENAI_DEPLOYMENT`
   - `AZURE_OPENAI_API_VERSION` (e.g. `2024-10-21`)

4. **Verify the data file is in place:**

   `data/features.json` ships with the repo. The first time you save a chart, `data/saved_charts.json` will be created automatically.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Open `http://localhost:8501`. The app starts on the **Conversation** tab. Switch to the **Dashboard** tab to see saved charts.

## Manual smoke tests

### Setup
1. Empty `.env` -> expect a config error panel.
2. Move `data/features.json` aside, restart -> expect a data error panel.
3. Restore the file, restart -> expect the app to load on the Conversation tab.

### Conversation - feature recognition
4. Ask "Show me MRR" -> expect a line chart from F001.
5. Ask "Customer churn trend" -> expect a line chart from F002.
6. Ask "Revenue by plan tier" -> expect a bar chart from F003.
7. Ask "Sales funnel" -> expect a funnel chart from F009.
8. Ask "Feature adoption" -> expect a horizontal bar chart from F010, sorted descending.
9. Ask "New vs returning customers" -> expect a grouped bar chart from F004.

### Conversation - stats
10. Ask "What's the average MRR for the year?" -> expect a number in text, no chart, soft offer to chart.
11. Ask "Median DAU in December" -> expect compute_stats invoked, single-number reply.
12. Ask vague: "How are we doing on engagement?" -> expect the model to pick F008/F010/F014.

### Conversation - dashboard request
13. Ask "Build me a dashboard" -> expect 4-5 charts in a 2-column grid spanning categories.

### Conversation - multi-feature comparison
14. Ask "Compare MRR with churn" -> expect TWO separate charts. Never combined.

### Conversation - safety
15. Ask "Marketing spend by region" (no such feature) -> expect a graceful "I don't have that data" reply with closest matches.

### Per-chart actions
16. Edit a chart's name field -> expect the new name to display immediately.
17. Hover Plotly toolbar, click camera/download icon -> expect a PNG download.
18. Click "Save to Dashboard" -> expect button to flip to "Saved ✓" and become disabled.
19. Re-click the disabled button -> no-op.

### Dashboard tab
20. Open Dashboard with no saves -> expect empty-state message.
21. Save 3 charts via Conversation, switch to Dashboard -> expect them in 2-column grid, newest first.
22. Edit `data/features.json` December MRR to `999000`, click sidebar "Reload data", switch to Dashboard -> expect saved MRR chart's December value to show 999k.
23. Rename a saved chart -> reload page -> name persists.
24. Click delete on a saved chart -> disappears from grid and from `data/saved_charts.json`.

### Persistence and graceful failures
25. Save a chart from F007, remove F007 from `features.json`, click "Reload data" -> that tile shows "Source feature no longer available" with Delete button. Other tiles render.
26. Save 2 charts. Restart Streamlit. Open Dashboard -> both still there.
27. Corrupt `data/saved_charts.json` (delete a closing brace), restart, open Dashboard -> empty state plus a `data/saved_charts.corrupt-<timestamp>.json` backup file exists.

## Project layout

```
app.py                 - Streamlit shell (tabs, env validation)
views/                 - conversation.py, dashboard.py (per-tab logic)
agent/                 - Azure OpenAI client, tool-calling loop, prompts, tool impls
features/              - features.json loader and catalog text builder
charts/                - Plotly renderer (incl. funnel/grouped_bar/horizontal_bar) + per-chart actions
dashboard/             - saved-chart persistence (atomic JSON file)
data/                  - features.json (source) + saved_charts.json (generated)
docs/superpowers/      - specs and implementation plans
```

## Adding new features (metrics)

Edit `data/features.json`. Each entry needs:

- `feature_id` (unique, e.g. `F016`)
- `feature_name`
- `feature_description`
- `category`
- `tags` (list of strings)
- `suggested_chart` (one of bar, line, scatter, pie, histogram, box, heatmap, funnel, grouped_bar, horizontal_bar) - optional; the model infers when omitted
- `x_field`, `y_field` or `y_fields` - optional axis hints
- `data` - non-empty list of objects (dict per row)

Restart the app or click sidebar "Reload data" to pick up changes.

## Troubleshooting

- "Missing required environment variables" -> copy `.env.example` to `.env` and fill it in.
- "Invalid data/features.json" -> check the error message; entry order is preserved.
- "Authentication failed" -> check API key and endpoint in `.env`.
- "Rate limit hit" -> wait a few seconds; the app retries once automatically.
- Charts won't render -> confirm the deployment supports function calling.