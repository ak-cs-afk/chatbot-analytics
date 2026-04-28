from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterator

from openai import AzureOpenAI, APIError, RateLimitError, APITimeoutError, AuthenticationError

from agent.prompts import build_system_prompt
from agent.tools import TOOLS, dispatch, parse_tool_arguments
from datasets.base import Dataset


logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
RATE_LIMIT_RETRY_DELAY_SECONDS = 2


@dataclass
class AssistantTurn:
    text: str = ""
    charts: list = field(default_factory=list)   # list of plotly Figures
    tables: list[dict] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


@dataclass
class ProgressUpdate:
    """Yielded by AnalyticsAgent.run_streaming so the UI can show activity."""
    label: str


class AnalyticsAgent:
    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        self.client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        )

    def system_prompt(self) -> str:
        return build_system_prompt(
            dataset_name=self.dataset.name,
            dataset_description=self.dataset.description,
            schema_summary=self.dataset.schema_summary(),
        )

    def run_streaming(
        self, user_message: str, history: list[dict]
    ) -> Iterator[ProgressUpdate | AssistantTurn]:
        """Run the agent loop, yielding progress updates and a final AssistantTurn."""
        turn = AssistantTurn()
        messages: list[dict] = [{"role": "system", "content": self.system_prompt()}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        for iteration in range(MAX_TOOL_ITERATIONS):
            yield ProgressUpdate(label=f"Thinking (step {iteration + 1})...")

            try:
                response = self._chat(messages)
            except _FriendlyAPIError as exc:
                turn.error = exc.message
                turn.text = exc.message
                yield turn
                return

            choice = response.choices[0]
            assistant_message = choice.message

            if assistant_message.tool_calls:
                # The model wants to call tools. Append the assistant message,
                # execute each call, append tool results, then loop.
                messages.append(_assistant_message_dict(assistant_message))

                for tc in assistant_message.tool_calls:
                    name = tc.function.name
                    args = parse_tool_arguments(tc.function.arguments)
                    yield ProgressUpdate(label=_progress_label(name))

                    logger.info("tool call: %s args_keys=%s", name, list(args.keys()))
                    result = dispatch(name, args, self.dataset, turn)
                    logger.info(
                        "tool result: %s ok=%s",
                        name,
                        result.get("ok") if isinstance(result, dict) else "?",
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=str),
                        }
                    )
                continue

            # Final assistant turn (no tool calls).
            turn.text = assistant_message.content or ""
            yield turn
            return

        # Loop cap hit.
        turn.truncated = True
        turn.text = (
            "I tried several approaches but couldn't fully resolve this. "
            "Here is the partial result based on what I gathered."
        )
        yield turn

    def _chat(self, messages: list[dict]):
        try:
            return self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
        except RateLimitError:
            time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
            try:
                return self.client.chat.completions.create(
                    model=self.deployment,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    temperature=0.2,
                )
            except RateLimitError as exc:
                raise _FriendlyAPIError(
                    "Azure OpenAI rate limit hit. Try again in a few seconds."
                ) from exc
        except AuthenticationError as exc:
            raise _FriendlyAPIError(
                "Azure OpenAI authentication failed. Check AZURE_OPENAI_API_KEY in .env."
            ) from exc
        except APITimeoutError as exc:
            raise _FriendlyAPIError(
                "Azure OpenAI request timed out. Try again."
            ) from exc
        except APIError as exc:
            raise _FriendlyAPIError(f"Azure OpenAI error: {exc}") from exc


# ---------- helpers ----------

class _FriendlyAPIError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _assistant_message_dict(msg) -> dict:
    """Convert an OpenAI ChatCompletionMessage with tool_calls to a serializable dict."""
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (msg.tool_calls or [])
        ],
    }


def _progress_label(tool_name: str) -> str:
    return {
        "run_sql": "Running SQL...",
        "make_chart": "Building chart...",
        "compute_stats": "Computing statistics...",
    }.get(tool_name, f"Running {tool_name}...")