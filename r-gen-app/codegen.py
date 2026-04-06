"""
codegen.py

Sends a parsed file summary and a user question to the Ollama cloud API and
streams back generated R code chunk by chunk.

The Ollama chat endpoint is OpenAI-compatible; we use the streaming form so
the UI can display tokens progressively rather than waiting for the full
response.
"""

import json
import os
from typing import AsyncGenerator

import httpx
from dotenv import load_dotenv

# Load OLLAMA_API_KEY and OLLAMA_BASE_URL from the .env file (or the process
# environment if already set — load_dotenv() does not override existing vars).
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration — read once at import time so every call pays no I/O cost.
# ---------------------------------------------------------------------------

_API_KEY: str = os.environ.get("OLLAMA_API_KEY", "")
_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

# The model tag to request from Ollama.  Override via environment if needed.
_MODEL: str = os.environ.get("OLLAMA_MODEL", "llama3")

# Seconds to wait for the server to start returning tokens.  Streaming
# responses can be slow to initiate on large models.
_CONNECT_TIMEOUT: float = 30.0
_READ_TIMEOUT: float = 120.0

# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert R programmer specializing in data cleaning and exploratory data analysis.
The user has uploaded a dataset. Here is everything you know about it:

{file_summary_json}

Your job is to write clean, well-commented R code that answers the user's question about this dataset.

Rules you must follow:
- Always start with library() calls for any packages used (tidyverse, janitor, skimr, etc.)
- Use tidyverse style throughout (dplyr, tidyr, ggplot2 where needed)
- Assume the data is loaded as a dataframe called `df` — always start the script with:
  df <- read_csv("your_file.csv")  # or read_excel() for xlsx
- Add a comment above every logical block explaining what it does and why
- If the user's question is ambiguous, make a reasonable assumption and add a comment noting it
- Output ONLY valid R code — no prose, no markdown fences, no explanation outside of code comments
- If the question is outside the scope of data cleaning or EDA, respond with a single R comment:
  # This request is outside the scope of this tool.
"""


def _build_system_prompt(file_summary: dict) -> str:
    """
    Serialize *file_summary* to a compact, readable JSON string and inject it
    into the system prompt template.

    indent=2 keeps it human-readable (helps the model reason about structure)
    while staying reasonably concise.
    """
    summary_json = json.dumps(file_summary, indent=2)
    return _SYSTEM_PROMPT_TEMPLATE.format(file_summary_json=summary_json)


def _build_request_body(system_prompt: str, user_question: str) -> dict:
    """
    Construct the JSON payload for the Ollama /api/chat endpoint.

    The Ollama streaming chat API mirrors OpenAI's chat/completions shape,
    so the messages array has the same role/content structure.  Setting
    stream=True causes the server to emit newline-delimited JSON objects
    (one per token batch) instead of a single response body.
    """
    return {
        "model": _MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_question},
        ],
    }


def _build_headers() -> dict:
    """
    Build request headers.  The Authorization header is only included when an
    API key is configured — local Ollama instances typically do not need one.
    """
    headers = {"Content-Type": "application/json"}
    if _API_KEY:
        headers["Authorization"] = f"Bearer {_API_KEY}"
    return headers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def stream_r_code(
    file_summary: dict,
    user_question: str,
) -> AsyncGenerator[str, None]:
    """
    Async generator that streams R code chunks from the Ollama API.

    Each ``yield`` delivers the text fragment contained in one server-sent
    JSON object.  The caller can forward these chunks directly to a
    streaming HTTP response (e.g. FastAPI's StreamingResponse) or
    accumulate them into a complete script.

    Parameters
    ----------
    file_summary : dict
        The structured dataset summary returned by file_parser.parse_file().
    user_question : str
        The natural-language question the user typed in the UI.

    Yields
    ------
    str
        A text fragment (one or more tokens) as it arrives from the API.

    Raises
    ------
    RuntimeError
        Wraps any httpx or API-level error with a user-friendly message so
        the caller does not need to handle low-level HTTP exceptions.
    """
    system_prompt = _build_system_prompt(file_summary)
    headers = _build_headers()
    body = _build_request_body(system_prompt, user_question)

    # ---------------------------------------------------------------------------
    # Endpoint URL construction
    #
    # Ollama (both local and cloud at ollama.com) uses /api/chat.
    # OLLAMA_BASE_URL can be set to either:
    #   - https://ollama.com/api   → append /chat
    #   - http://localhost:11434   → append /api/chat
    #
    # Rule: if the base URL already ends with /api, append /chat only.
    # Otherwise append /api/chat (the full path from a bare host URL).
    # ---------------------------------------------------------------------------
    base = _BASE_URL.rstrip("/")
    if base.endswith("/api"):
        url = f"{base}/chat"
    else:
        url = f"{base}/api/chat"

    print(f"[codegen] POST {url}  model={_MODEL}")
    auth_display = ("Bearer " + _API_KEY[:6] + "…") if _API_KEY else "(none)"
    print(f"[codegen] Authorization: {auth_display}")

    # Use a generous timeout tuple: (connect_timeout, read_timeout).
    # The read timeout applies to each individual chunk, not the full stream,
    # so a slow model still has time to emit tokens incrementally.
    timeout = httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=10.0, pool=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                print(f"[codegen] HTTP {response.status_code}")

                # Surface 4xx / 5xx before we start reading the body, so the
                # error message can include the status code and reason phrase.
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise RuntimeError(
                        f"Ollama API returned {response.status_code}: "
                        f"{error_body.decode(errors='replace')}"
                    )

                line_count = 0
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue

                    # OpenAI-compatible streams prefix lines with "data: "
                    if line.startswith("data: "):
                        line = line[6:]

                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Ollama native format: {"message":{"content":"..."},"done":false}
                    content = chunk.get("message", {}).get("content", "")
                    done = chunk.get("done", False)

                    if content:
                        line_count += 1
                        if line_count == 1:
                            print(f"[codegen] first content token: {content!r}")
                        yield content

                    if done:
                        print(f"[codegen] stream done — {line_count} content lines yielded")
                        break

    except RuntimeError:
        # Re-raise our own clean errors unchanged.
        raise

    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama at {_BASE_URL}. "
            "Check that OLLAMA_BASE_URL is correct and the server is running."
        ) from exc

    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "The request to Ollama timed out. "
            "The model may be loading or the server is under heavy load — please try again."
        ) from exc

    except httpx.HTTPError as exc:
        # Catch-all for any other httpx transport error (SSL, redirect loops, etc.).
        raise RuntimeError(
            f"An unexpected network error occurred while contacting Ollama: {exc}"
        ) from exc
