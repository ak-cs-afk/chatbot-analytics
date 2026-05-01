from __future__ import annotations

import logging
import os

import streamlit as st
from dotenv import load_dotenv

from features.loader import (
    DEFAULT_PATH as FEATURES_PATH,
    FeaturesValidationError,
    load_features,
    reload_features,
)
from views import conversation, dashboard


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


def main() -> None:
    st.set_page_config(
        page_title="Chatbot Analytics",
        page_icon=":bar_chart:",
        layout="wide",
    )
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


def _check_config() -> bool:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        st.error(
            "Missing required environment variables. Copy `.env.example` to `.env` and fill in:\n\n"
            + "\n".join(f"- `{v}`" for v in missing)
        )
        return False
    return True


def _check_features() -> bool:
    try:
        load_features()
        return True
    except FeaturesValidationError as exc:
        st.error(
            f"Invalid `{FEATURES_PATH}`: {exc}\n\n"
            "Make sure the file exists and each entry has `feature_id`, `feature_name`, and a non-empty `data` array."
        )
        return False


def _render_sidebar() -> None:
    with st.sidebar:
        st.radio(
            "View",
            options=["Conversation", "Dashboard"],
            index=0,
            key="active_view",
        )
        st.divider()

        st.header("Settings")
        st.text_input(
            "Deployment",
            value=os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
            disabled=True,
        )

        if st.button("Reload data", use_container_width=True):
            try:
                reload_features()
                st.toast("Features reloaded from disk.")
            except FeaturesValidationError as exc:
                st.error(f"Reload failed: {exc}")

        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.caption("v3 - recipe-based")


if __name__ == "__main__":
    main()