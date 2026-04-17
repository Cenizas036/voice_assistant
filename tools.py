"""
tools.py — Intent Execution Tools
====================================
Each recognised intent is handled by a dedicated function. All functions
return a ``ToolResult`` dataclass so the UI layer has a consistent contract.

Supported intents
-----------------
    create_file  — creates an empty file at the requested path
    write_code   — asks Ollama to generate code, then optionally saves it
    summarize    — asks Ollama to summarize a topic or text block
    chat         — free-form conversation via Ollama
    compound     — sequentially executes multiple sub-intents

Bonus features wired here
--------------------------
    • Human-in-the-loop: create_file and write_code return a
      ``needs_confirmation`` flag so the UI can prompt the user first.
    • Graceful degradation: if Ollama is unreachable, tools return a
      helpful error string rather than raising.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field
from typing import Any

import requests

from intent import OLLAMA_BASE_URL, OLLAMA_MODEL, REQUEST_TIMEOUT

# ---------------------------------------------------------------------------
# Output directory for created / generated files
# ---------------------------------------------------------------------------
OUTPUTS_DIR: str = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    """Unified return value from every tool function."""

    intent: str = ""
    success: bool = True
    message: str = ""          # Human-readable summary
    code: str = ""             # Generated source code (write_code)
    file_path: str = ""        # Path of created / written file
    needs_confirmation: bool = False  # Human-in-the-loop flag
    confirmation_prompt: str = ""     # Question to show the user
    sub_results: list["ToolResult"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. create_file
# ---------------------------------------------------------------------------

def create_file(
    filename: str | None,
    confirmed: bool = False,
) -> ToolResult:
    """
    Create an empty file.

    If *confirmed* is False the function returns with
    ``needs_confirmation=True`` so the UI asks the user first.
    """
    if not filename:
        return ToolResult(
            intent="create_file",
            success=False,
            message="No filename was specified. Please say something like "
                    "'create a file called hello.py'.",
        )

    safe_name = os.path.basename(filename)
    target = os.path.join(OUTPUTS_DIR, safe_name)

    if not confirmed:
        return ToolResult(
            intent="create_file",
            success=True,
            needs_confirmation=True,
            confirmation_prompt=(
                f"I'm about to create the file **{safe_name}** in the "
                f"outputs folder.\n\nPath: `{target}`\n\n"
                f"Should I go ahead?"
            ),
            file_path=target,
            message=f"Waiting for confirmation to create {safe_name}.",
        )

    try:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("")
        return ToolResult(
            intent="create_file",
            success=True,
            message=f"✅ File created: `{target}`",
            file_path=target,
        )
    except Exception as exc:
        return ToolResult(
            intent="create_file",
            success=False,
            message=f"❌ Failed to create file: {exc}",
        )


# ---------------------------------------------------------------------------
# 2. write_code
# ---------------------------------------------------------------------------

def write_code(
    language: str | None,
    description: str | None,
    filename: str | None = None,
    confirmed: bool = False,
    user_text: str = "",
) -> ToolResult:
    """
    Ask Ollama to generate code, then optionally save it to a file.

    Human-in-the-loop: when *confirmed* is False, the generated code
    is returned with ``needs_confirmation=True`` so the user can review
    before it's saved to disk.
    """
    lang = language or "python"
    desc = description or user_text or "a simple hello-world program"

    prompt = (
        f"Write clean, production-quality {lang} code that does the following:\n\n"
        f"{desc}\n\n"
        f"Return ONLY the code — no explanations, no markdown fences."
    )

    code, err = _ask_ollama(prompt)
    if err:
        return ToolResult(
            intent="write_code",
            success=False,
            message=f"❌ Code generation failed: {err}",
        )

    # Strip accidental markdown fences from model output.
    code = _strip_code_fences(code)

    # Determine output filename.
    ext_map = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "java": ".java", "c": ".c", "cpp": ".cpp", "c++": ".cpp",
        "go": ".go", "rust": ".rs", "ruby": ".rb", "html": ".html",
        "css": ".css", "bash": ".sh", "shell": ".sh", "sql": ".sql",
    }
    ext = ext_map.get(lang.lower(), ".txt")
    out_name = filename or f"generated_code{ext}"
    safe_name = os.path.basename(out_name)
    target = os.path.join(OUTPUTS_DIR, safe_name)

    if not confirmed:
        return ToolResult(
            intent="write_code",
            success=True,
            needs_confirmation=True,
            confirmation_prompt=(
                f"I generated **{lang}** code for: *{desc}*\n\n"
                f"Review the code panel and confirm to save it as "
                f"`{safe_name}`."
            ),
            code=code,
            file_path=target,
            message="Code generated — awaiting confirmation to save.",
        )

    # Save to disk after confirmation.
    try:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(code)
        return ToolResult(
            intent="write_code",
            success=True,
            message=f"✅ Code saved to `{target}`",
            code=code,
            file_path=target,
        )
    except Exception as exc:
        return ToolResult(
            intent="write_code",
            success=False,
            message=f"❌ Could not save file: {exc}",
            code=code,
        )


# ---------------------------------------------------------------------------
# 3. summarize
# ---------------------------------------------------------------------------

def summarize(topic: str | None, description: str | None = None) -> ToolResult:
    """Ask Ollama to summarize a topic or block of text."""
    text = description or topic or ""
    if not text.strip():
        return ToolResult(
            intent="summarize",
            success=False,
            message="Nothing to summarize. Please provide a topic or text.",
        )

    prompt = (
        f"Provide a clear, concise summary of the following:\n\n"
        f"{text}\n\n"
        f"Use bullet points where appropriate."
    )
    summary, err = _ask_ollama(prompt)
    if err:
        return ToolResult(
            intent="summarize",
            success=False,
            message=f"❌ Summarization failed: {err}",
        )
    return ToolResult(
        intent="summarize",
        success=True,
        message=summary,
    )


# ---------------------------------------------------------------------------
# 4. chat
# ---------------------------------------------------------------------------

def chat(
    user_text: str,
    conversation_context: str = "",
) -> ToolResult:
    """General-purpose conversation."""
    if not user_text.strip():
        return ToolResult(
            intent="chat",
            success=True,
            message="I didn't catch that. Could you repeat?",
        )

    parts: list[str] = []
    if conversation_context:
        parts.append(
            f"Here is the recent conversation for context:\n"
            f"{conversation_context}\n\n"
        )
    parts.append(f"User says: {user_text}\n\nRespond helpfully and concisely.")
    prompt = "".join(parts)

    reply, err = _ask_ollama(prompt)
    if err:
        return ToolResult(
            intent="chat",
            success=False,
            message=f"❌ Chat failed (Ollama error): {err}\n\n"
                    f"Falling back: I received your message \"{user_text}\" "
                    f"but the LLM is unavailable right now.",
        )
    return ToolResult(
        intent="chat",
        success=True,
        message=reply,
    )


# ---------------------------------------------------------------------------
# 5. compound — execute multiple sub-intents sequentially
# ---------------------------------------------------------------------------

def execute_compound(sub_intents: list[dict[str, Any]], user_text: str) -> ToolResult:
    """
    Run each sub-intent in order and aggregate results.
    """
    if not sub_intents:
        return ToolResult(
            intent="compound",
            success=False,
            message="No sub-intents found in the compound command.",
        )

    sub_results: list[ToolResult] = []
    messages: list[str] = []

    for idx, si in enumerate(sub_intents, start=1):
        intent_name = si.get("intent", "chat")
        entities = si.get("entities", {})
        header = f"**[Step {idx}: {intent_name}]**"

        result = dispatch_intent(
            intent_name=intent_name,
            entities=entities,
            user_text=user_text,
            confirmed=False,  # Each sub-step still needs confirmation if applicable.
        )
        sub_results.append(result)
        messages.append(f"{header}\n{result.message}")

    combined_msg = "\n\n---\n\n".join(messages)
    overall_success = all(r.success for r in sub_results)

    return ToolResult(
        intent="compound",
        success=overall_success,
        message=combined_msg,
        sub_results=sub_results,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch_intent(
    intent_name: str,
    entities: dict[str, Any],
    user_text: str = "",
    conversation_context: str = "",
    confirmed: bool = False,
) -> ToolResult:
    """Route an intent name to the correct tool function."""
    if intent_name == "create_file":
        return create_file(
            filename=entities.get("filename"),
            confirmed=confirmed,
        )
    elif intent_name == "write_code":
        return write_code(
            language=entities.get("language"),
            description=entities.get("description"),
            filename=entities.get("filename"),
            confirmed=confirmed,
            user_text=user_text,
        )
    elif intent_name == "summarize":
        return summarize(
            topic=entities.get("topic"),
            description=entities.get("description"),
        )
    elif intent_name == "compound":
        return execute_compound(
            sub_intents=entities.get("sub_intents", []),
            user_text=user_text,
        )
    else:
        # Default to chat.
        return chat(
            user_text=user_text,
            conversation_context=conversation_context,
        )


# ---------------------------------------------------------------------------
# Ollama helper
# ---------------------------------------------------------------------------

def _ask_ollama(prompt: str) -> tuple[str, str]:
    """
    Send a prompt to Ollama and return ``(response_text, error_string)``.
    On success *error_string* is empty. On failure *response_text* is empty.
    """
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 1024},
        }
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        text = r.json().get("response", "").strip()
        return text, ""
    except requests.ConnectionError:
        return "", (
            "Cannot reach Ollama. Make sure it is running with "
            f"`ollama serve` on {OLLAMA_BASE_URL}."
        )
    except requests.Timeout:
        return "", f"Ollama timed out after {REQUEST_TIMEOUT}s."
    except Exception:
        return "", traceback.format_exc()


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that LLMs love to add."""
    import re

    text = text.strip()
    text = re.sub(r"^```[\w]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()
