"""
intent.py — Intent Classification via Ollama LLM
===================================================
Sends the transcribed text to a local Ollama model and asks it to classify
the user's intent into one of five categories:

    1. create_file   — create an empty file at a given path
    2. write_code    — generate source code for a described task
    3. summarize     — summarize a block of text or a topic
    4. chat          — general-purpose conversation
    5. compound      — the utterance contains MULTIPLE intents above

The response from Ollama is expected as strict JSON:

    {
      "intent": "<intent_name>",
      "confidence": 0.0-1.0,
      "entities": {
        "filename": "...",      // for create_file
        "language": "...",      // for write_code
        "description": "...",   // for write_code / summarize
        "topic": "...",         // for summarize / chat
        "sub_intents": [...]    // for compound
      }
    }

Graceful degradation:
    - If Ollama is unreachable the module returns a fallback
      intent of ``chat`` so the pipeline never crashes.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "phi3")
REQUEST_TIMEOUT: int = int(os.environ.get("OLLAMA_TIMEOUT", "600"))

SYSTEM_PROMPT: str = """You are an intent-classification engine.

Given the user's spoken command, respond with ONLY a JSON object — no markdown
fences, no commentary, no extra text. Use this exact schema:

{
  "intent": "<create_file | write_code | summarize | chat | compound>",
  "confidence": <float between 0 and 1>,
  "entities": {
    "filename": "<string or null>",
    "language": "<programming language or null>",
    "description": "<string or null>",
    "topic": "<string or null>",
    "sub_intents": [
      {
        "intent": "<intent>",
        "confidence": <float>,
        "entities": { ... }
      }
    ]
  }
}

Rules:
- "sub_intents" must be an array (empty if not compound).
- If the user asks to do MORE THAN ONE distinct action (e.g. "create a file
  AND write some Python code"), set intent to "compound" and list each
  sub-action inside "sub_intents".
- If the utterance is ambiguous, pick "chat" and set confidence < 0.5.
- For create_file, extract the desired filename into entities.filename.
- For write_code, extract the language and a description of the code.
- For summarize, extract the topic or paste to summarize.
- Always return valid JSON. No extra keys, no trailing commas.
"""

# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

FALLBACK_INTENT: dict[str, Any] = {
    "intent": "chat",
    "confidence": 0.0,
    "entities": {
        "filename": None,
        "language": None,
        "description": None,
        "topic": None,
        "sub_intents": [],
    },
    "_fallback": True,
    "_error": "",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_intent(
    user_text: str,
    conversation_context: str = "",
) -> dict[str, Any]:
    """
    Send *user_text* to Ollama and return a parsed intent dict.

    Parameters
    ----------
    user_text : str
        The transcribed (or typed) user command.
    conversation_context : str
        Optional recent conversation history to include for better
        context-aware classification.

    Returns
    -------
    dict
        Parsed intent dictionary following the schema above.
        On failure, returns ``FALLBACK_INTENT`` with the error attached.
    """
    if not user_text or not user_text.strip():
        fallback = _make_fallback("Empty input received.")
        return fallback

    prompt_parts: list[str] = []
    if conversation_context:
        prompt_parts.append(
            f"Recent conversation for context:\n{conversation_context}\n\n"
        )
    prompt_parts.append(f"User command to classify:\n{user_text}")
    full_prompt = "".join(prompt_parts)

    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": full_prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 512,
            },
        }
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        raw_response: str = resp.json().get("response", "")
        parsed = _parse_json_response(raw_response)
        return parsed

    except requests.ConnectionError:
        return _make_fallback(
            "Ollama is not reachable. Is it running on "
            f"{OLLAMA_BASE_URL}?"
        )
    except requests.Timeout:
        return _make_fallback(
            f"Ollama request timed out after {REQUEST_TIMEOUT}s."
        )
    except Exception:
        return _make_fallback(traceback.format_exc())


def is_ollama_available() -> bool:
    """Quick health-check for the Ollama server."""
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict[str, Any]:
    """
    Attempt to extract a JSON object from the LLM's raw text output.
    Handles cases where the model wraps JSON in markdown fences.
    """
    # Strip markdown code fences if present.
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first { ... } block.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return _make_fallback(
                    f"Could not parse JSON from LLM output:\n{raw[:500]}"
                )
        else:
            return _make_fallback(
                f"No JSON object found in LLM output:\n{raw[:500]}"
            )

    # Validate minimal schema.
    if "intent" not in parsed:
        parsed["intent"] = "chat"
    if "confidence" not in parsed:
        parsed["confidence"] = 0.5
    if "entities" not in parsed:
        parsed["entities"] = {}
    ent = parsed["entities"]
    ent.setdefault("filename", None)
    ent.setdefault("language", None)
    ent.setdefault("description", None)
    ent.setdefault("topic", None)
    ent.setdefault("sub_intents", [])

    parsed["_fallback"] = False
    parsed["_error"] = ""
    return parsed


def _make_fallback(error_msg: str) -> dict[str, Any]:
    fb = dict(FALLBACK_INTENT)
    fb["entities"] = dict(FALLBACK_INTENT["entities"])
    fb["_error"] = error_msg
    return fb
