from __future__ import annotations

import copy
import datetime
import json
import os
import uuid
from typing import Any

MAX_TURNS: int = 20
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")


class SessionMemory:
    """Thread-safe, bounded conversation memory for a single session."""
    def __init__(self, session_id: str, topic: str = "New Chat", max_turns: int = MAX_TURNS) -> None:
        self.session_id = session_id
        self.topic = topic
        self._max_turns: int = max_turns
        self._history: list[dict[str, Any]] = []

    def set_topic_from_text(self, text: str) -> None:
        words = text.split()
        if not words:
            self.topic = "Empty Chat"
        else:
            self.topic = " ".join(words[:4]) + ("..." if len(words) > 4 else "")

    def add_user_turn(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        # Generate topic on the very first user message
        if len(self._history) == 0:
            self.set_topic_from_text(content)
        self._append("user", content, metadata)

    def add_assistant_turn(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        self._append("assistant", content, metadata)

    def get_history(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._history)

    def format_for_llm(self, last_n: int = 6) -> str:
        lines: list[str] = []
        for turn in self._history[-last_n:]:
            role = turn["role"].upper()
            text = turn["content"]
            lines.append(f"[{role}]: {text}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._history.clear()

    @property
    def turn_count(self) -> int:
        return len(self._history)

    def _append(self, role: str, content: str, metadata: dict[str, Any] | None) -> None:
        entry: dict[str, Any] = {
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self._history.append(entry)
        while len(self._history) > self._max_turns:
            self._history.pop(0)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "max_turns": self._max_turns,
            "history": self._history
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionMemory:
        sm = cls(
            session_id=data["session_id"], 
            topic=data.get("topic", "New Chat"), 
            max_turns=data.get("max_turns", MAX_TURNS)
        )
        sm._history = data.get("history", [])
        return sm


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, SessionMemory] = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self.load()

    def get_or_create(self, session_id: str | None = None) -> SessionMemory:
        if not session_id or session_id not in self.sessions:
            session_id = str(uuid.uuid4())
            self.sessions[session_id] = SessionMemory(session_id)
            self.save()
        return self.sessions[session_id]

    def delete_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]
            self.save()

    def get_all_sessions_meta(self) -> list[dict]:
        meta = []
        # Read items in reverse order (newest first conceptually, though dict is insertion order)
        for s_id, sm in reversed(list(self.sessions.items())):
            meta.append({"id": s_id, "topic": sm.topic, "turn_count": sm.turn_count})
        return meta

    def load(self):
        if os.path.exists(SESSIONS_FILE):
            try:
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for sid, sdata in data.items():
                        self.sessions[sid] = SessionMemory.from_dict(sdata)
            except Exception as e:
                print("Failed to load sessions:", e)

    def save(self):
        try:
            data = {sid: sm.to_dict() for sid, sm in self.sessions.items()}
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print("Failed to save sessions:", e)