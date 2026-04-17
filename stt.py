"""
stt.py — Speech-to-Text Module
================================
Uses OpenAI's Whisper (local, no API key) for transcription.

Supports two input modes:
    1. Microphone recording  — Gradio supplies a file path to a .wav
    2. File upload           — Gradio supplies a file path to any audio format

Graceful degradation:
    - If Whisper fails to load (e.g. no GPU, missing ffmpeg), the module
      exposes a fallback that returns an error string instead of crashing.
"""

from __future__ import annotations

import os
import traceback
from typing import Optional

# ---------------------------------------------------------------------------
# Lazy-loaded Whisper model (singleton)
# ---------------------------------------------------------------------------
_whisper_model = None
_whisper_load_error: Optional[str] = None
_MODEL_SIZE: str = os.environ.get("WHISPER_MODEL_SIZE", "base")


def _load_whisper():
    """Load the Whisper model lazily on first call."""
    global _whisper_model, _whisper_load_error
    if _whisper_model is not None:
        return _whisper_model
    if _whisper_load_error is not None:
        return None
    try:
        import whisper  # type: ignore

        _whisper_model = whisper.load_model(_MODEL_SIZE)
        return _whisper_model
    except Exception:
        _whisper_load_error = traceback.format_exc()
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transcribe_audio(audio_path: Optional[str]) -> str:
    """
    Transcribe an audio file at *audio_path* using local Whisper.

    Parameters
    ----------
    audio_path : str | None
        Absolute path to an audio file (.wav, .mp3, .m4a, .webm, etc.).
        ``None`` when the user hasn't provided any audio.

    Returns
    -------
    str
        The transcribed text, or an error / informational message.
    """
    if audio_path is None or not audio_path:
        return ""

    if not os.path.isfile(audio_path):
        return f"[STT ERROR] File not found: {audio_path}"

    model = _load_whisper()
    if model is None:
        return (
            f"[STT ERROR] Whisper failed to load.\n"
            f"Details:\n{_whisper_load_error}\n\n"
            f"Tip: Make sure you have ffmpeg installed and sufficient RAM/VRAM."
        )

    try:
        result = model.transcribe(audio_path, fp16=False)
        text: str = result.get("text", "").strip()
        if not text:
            return "[STT] No speech detected in the audio."
        return text
    except Exception as exc:
        return f"[STT ERROR] Transcription failed: {exc}"


def transcribe_microphone(mic_audio_path: Optional[str]) -> str:
    """
    Convenience wrapper — Gradio's microphone component yields
    a file path (wav) just like file upload.
    """
    return transcribe_audio(mic_audio_path)


def transcribe_file(file_path: Optional[str]) -> str:
    """
    Convenience wrapper for the file-upload input.
    """
    return transcribe_audio(file_path)


def is_whisper_available() -> bool:
    """Check whether Whisper loaded successfully."""
    _load_whisper()
    return _whisper_model is not None