from __future__ import annotations

import logging
import os

import streamlit as st
from dotenv import load_dotenv

from agent.client import AnalyticsAgent, AssistantTurn, ProgressUpdate
from datasets.northwind import NorthwindDataset
from datasets.olist import OlistDataset


load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)


REQUIRED_ENV_VARS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
]

DATASETS = {
    "Northwind": NorthwindDataset,
    "Olist E-Commerce": OlistDataset,
}


def main() -> None:
    st.set_page_config(page_title="Chatbot Analytics", page_icon=":bar_chart:", layout="wide")
    st.title("Chatbot Analytics")
    st.caption("Ask natural-language questions about your data. Get answers, charts, and dashboards.")

    if not _check_config():
        return

    if not _check_db():
        return

    _init_session_state()
    _render_sidebar()
    _render_history()
    _handle_input()


def _check_config() -> bool:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        st.error(
            "Missing required environment variables. "
            "Copy `.env.example` to `.env` and fill in:\n\n"
            + "\n".join(f"- `{v}`" for v in missing)
        )
        return False
    return True


def _check_db() -> bool:
    db_path = NorthwindDataset.db_path
    if not os.path.exists(db_path):
        st.error(
            f"Northwind database not found at `{db_path}`. "
            "Copy `Datasets/northwind/northwind.db` into `data/northwind.db`."
        )
        return False
    return True


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        # messages: list of {"role": "user"|"assistant", "text": str, "charts": [...], "tables": [...]}
        st.session_state.messages = []
    if "dataset_key" not in st.session_state:
        st.session_state.dataset_key = "Northwind"


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Dataset")
        keys = list(DATASETS.keys())
        # Build labels with disabled markers.
        instances = {k: DATASETS[k]() for k in keys}
        labels = {
            k: (f"{k}" if instances[k].enabled else f"{k} (coming soon)")
            for k in keys
        }
        # Streamlit radio doesn't natively disable individual options, so we
        # filter to enabled ones and show the disabled ones below as captions.
        enabled_keys = [k for k in keys if instances[k].enabled]
        choice = st.radio(
            "Active dataset",
            options=enabled_keys,
            format_func=lambda k: labels[k],
            index=enabled_keys.index(st.session_state.dataset_key)
            if st.session_state.dataset_key in enabled_keys
            else 0,
        )
        st.session_state.dataset_key = choice

        for k in keys:
            if not instances[k].enabled:
                st.caption(f":grey[{labels[k]}]")
                st.caption(f":grey[{instances[k].description}]")

        st.divider()
        st.header("Schema")
        with st.expander(f"{choice} tables", expanded=False):
            st.code(instances[choice].schema_summary(), language="text")

        st.divider()
        st.header("Settings")
        st.text_input(
            "Deployment",
            value=os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
            disabled=True,
        )
        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()


def _render_history() -> None:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg.get("text"):
                st.markdown(msg["text"])
            charts = msg.get("charts") or []
            if len(charts) >= 3:
                cols = st.columns(2)
                for i, fig in enumerate(charts):
                    with cols[i % 2]:
                        st.plotly_chart(fig, use_container_width=True, key=f"hist_{id(msg)}_{i}")
            else:
                for i, fig in enumerate(charts):
                    st.plotly_chart(fig, use_container_width=True, key=f"hist_{id(msg)}_{i}")
            tables = msg.get("tables") or []
            for i, t in enumerate(tables):
                with st.expander(f"Raw data: {t.get('title', f'table {i+1}')}"):
                    st.dataframe(
                        {col: [r[idx] for r in t["rows"]] for idx, col in enumerate(t["columns"])}
                        if t["columns"]
                        else {},
                        use_container_width=True,
                    )


def _handle_input() -> None:
    user_input = st.chat_input("Ask about your data...")
    if not user_input:
        return

    st.session_state.messages.append({"role": "user", "text": user_input})

    with st.chat_message("user"):
        st.markdown(user_input)

    dataset = DATASETS[st.session_state.dataset_key]()
    agent = AnalyticsAgent(dataset)

    history = _to_chat_history(st.session_state.messages[:-1])

    with st.chat_message("assistant"):
        status = st.status("Thinking...", expanded=False)
        final_turn: AssistantTurn | None = None
        try:
            for update in agent.run_streaming(user_input, history):
                if isinstance(update, ProgressUpdate):
                    status.update(label=update.label)
                else:
                    final_turn = update
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Unexpected error: {exc}")
            return

        if final_turn is None:
            status.update(label="No response", state="error")
            return

        status.update(
            label="Done" if not final_turn.error else "Failed",
            state="error" if final_turn.error else "complete",
        )

        if final_turn.text:
            st.markdown(final_turn.text)

        charts = final_turn.charts
        if len(charts) >= 3:
            cols = st.columns(2)
            for i, fig in enumerate(charts):
                with cols[i % 2]:
                    st.plotly_chart(fig, use_container_width=True, key=f"new_{i}")
        else:
            for i, fig in enumerate(charts):
                st.plotly_chart(fig, use_container_width=True, key=f"new_{i}")

        for i, t in enumerate(final_turn.tables):
            with st.expander(f"Raw data: {t.get('title', f'table {i+1}')}"):
                st.dataframe(
                    {col: [r[idx] for r in t["rows"]] for idx, col in enumerate(t["columns"])}
                    if t["columns"]
                    else {},
                    use_container_width=True,
                )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "text": final_turn.text,
            "charts": final_turn.charts,
            "tables": final_turn.tables,
        }
    )


def _to_chat_history(messages: list[dict]) -> list[dict]:
    """Convert the UI message list into the role/content format the agent expects.

    We strip charts/tables and only pass the text, since prior tool results are
    not replayed - each turn re-runs tools as needed.
    """
    out: list[dict] = []
    for m in messages:
        text = m.get("text") or ""
        if not text:
            continue
        out.append({"role": m["role"], "content": text})
    return out


if __name__ == "__main__":
    main()