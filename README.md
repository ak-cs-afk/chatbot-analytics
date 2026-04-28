# Chatbot Analytics

A conversational analytics app: ask natural-language questions about a dataset and get written insights, Plotly charts, dashboards, and statistical summaries via Azure OpenAI.

## Setup

1. **Python 3.11+** required.

2. **Create a virtual environment and install dependencies:**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Configure Azure OpenAI:**

   ```powershell
   Copy-Item .env.example .env
   ```

   Edit `.env` and fill in:

   - `AZURE_OPENAI_ENDPOINT` - your resource URL, e.g. `https://my-resource.openai.azure.com/`
   - `AZURE_OPENAI_API_KEY` - from the Azure portal
   - `AZURE_OPENAI_DEPLOYMENT` - the name of your chat deployment (must support tool/function calling)
   - `AZURE_OPENAI_API_VERSION` - e.g. `2024-10-21`

4. **Place the Northwind database:**

   ```powershell
   Copy-Item Datasets/northwind/northwind.db data/northwind.db
   ```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

The app opens at `http://localhost:8501`.

## Manual smoke tests

After the app is running:

1. **Counting:** Ask "How many orders are in Northwind?" - expect a single number plus brief text.
2. **Single chart:** Ask "Show me the top 5 products by revenue as a bar chart" - expect a chart plus a 1-3 sentence written insight.
3. **Dashboard:** Ask "Build me a sales dashboard" - expect 3 or more charts laid out in a 2-column grid.
4. **Stats only:** Ask "What's the average freight cost, and the min/max?" - expect numerical statistics in the reply.
5. **Stats with chart:** Ask "What's the distribution of freight costs? Add a histogram." - expect stats plus a histogram.
6. **Safety:** Ask "Drop the Customers table." - expect either a polite refusal in the reply or a graceful SQL error message.
7. **Sidebar:** Switch the dataset radio to "Olist E-Commerce" - expect it to be shown as disabled / "coming soon".
8. **Clear chat:** Click "Clear chat" in the sidebar - expect history to reset.

## Project layout

```
app.py                 - Streamlit UI
agent/                 - Azure OpenAI client, tool-calling loop, prompts, tool impls
datasets/              - Dataset abstraction (Northwind active, Olist stubbed)
charts/                - Plotly chart renderer
data/                  - SQLite database files
scripts/               - One-off scripts (Olist CSV-to-SQLite converter, planned)
docs/superpowers/      - Spec and implementation plan
```

## Adding the Olist dataset later

1. Implement `scripts/build_olist_db.py` that reads the 9 CSVs from `Datasets/olist-ecommerce/` and writes `data/olist.db` with proper foreign keys.
2. Replace the `OlistDataset` stub in `datasets/olist.py` with a concrete implementation modeled on `NorthwindDataset`.
3. Set `enabled = True` on the class. The sidebar will pick it up automatically.

## Troubleshooting

- **"Missing required environment variables"** - copy `.env.example` to `.env` and fill it in.
- **"Northwind database not found"** - run the `Copy-Item` command in setup step 4.
- **"Authentication failed"** - check your API key and endpoint in `.env`.
- **"Rate limit hit"** - wait a few seconds and retry. The app retries once automatically.
- **Charts don't render** - check that the active deployment supports function calling. Older `gpt-35-turbo` deployments may not.