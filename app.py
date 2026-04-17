from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from intent import classify_intent, is_ollama_available
from memory import SessionManager, MAX_TURNS
from stt import is_whisper_available, transcribe_audio
from tools import ToolResult, dispatch_intent, execute_compound


app = FastAPI(title="Voice Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_manager = SessionManager()

_pending_result: ToolResult | None = None
_pending_intent_data: dict | None = None
_pending_session_id: str | None = None


def _build_response_payload(result: ToolResult, transcript: str, intent_json: str, session) -> dict:
    global _pending_result, _pending_intent_data, _pending_session_id
    if result.needs_confirmation:
        _pending_result = result
        _pending_session_id = session.session_id
        response_text = result.confirmation_prompt
    else:
        _pending_result = None
        _pending_intent_data = None
        _pending_session_id = None
        response_text = result.message

    file_status = ""
    if result.file_path:
        file_status = f"{result.file_path}"
    if result.sub_results:
        parts = [sr.file_path for sr in result.sub_results if sr.file_path]
        if parts:
            file_status = "\n".join(parts)

    code_output = result.code
    if not code_output and result.sub_results:
        for sr in result.sub_results:
            if sr.code:
                code_output = sr.code
                break

    return {
        "session_id": session.session_id,
        "transcript": transcript,
        "intent_json": intent_json,
        "response_text": response_text,
        "code_output": code_output,
        "file_status": file_status,
        "history": session.get_history(),
        "needs_confirmation": result.needs_confirmation
    }


@app.post("/api/pipeline")
async def api_pipeline(
    session_id: str = Form(""),
    text_input: str = Form(""),
    audio_file: UploadFile | None = File(None)
):
    global _pending_intent_data
    
    session = session_manager.get_or_create(session_id if session_id else None)
    transcript = ""

    if text_input and text_input.strip():
        transcript = text_input.strip()
    elif audio_file and audio_file.filename:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            shutil.copyfileobj(audio_file.file, tmp)
            tmp_path = tmp.name
        transcript = transcribe_audio(tmp_path)
        try:
            os.remove(tmp_path)
        except:
            pass

    if not transcript:
        return {"error": "Nothing heard or typed.", "history": session.get_history()}

    context = session.format_for_llm(last_n=6)
    intent_data = classify_intent(transcript, conversation_context=context)
    intent_json = json.dumps(intent_data, indent=2, default=str)

    intent_name = intent_data.get("intent", "chat")
    entities = intent_data.get("entities", {})

    if intent_name == "compound":
        result = execute_compound(sub_intents=entities.get("sub_intents", []), user_text=transcript)
    else:
        result = dispatch_intent(
            intent_name=intent_name,
            entities=entities,
            user_text=transcript,
            conversation_context=context,
            confirmed=False,
        )

    if result.needs_confirmation:
        _pending_intent_data = intent_data

    session.add_user_turn(transcript, metadata={"intent": intent_name})
    session.add_assistant_turn(result.message, metadata={"intent": intent_name, "success": result.success})
    session_manager.save()

    return _build_response_payload(result, transcript, intent_json, session)


@app.post("/api/confirm")
async def api_confirm():
    global _pending_result, _pending_intent_data, _pending_session_id
    if _pending_result is None or _pending_intent_data is None or _pending_session_id is None:
        return {"error": "Nothing to confirm."}

    session = session_manager.get_or_create(_pending_session_id)
    intent_name = _pending_intent_data.get("intent", "chat")
    entities = _pending_intent_data.get("entities", {})

    if intent_name == "compound":
        sub_results = []
        messages = []
        codes = []
        for si in entities.get("sub_intents", []):
            res = dispatch_intent(
                intent_name=si.get("intent", "chat"),
                entities=si.get("entities", {}),
                confirmed=True,
            )
            sub_results.append(res)
            messages.append(res.message)
            if res.code:
                codes.append(res.code)
        
        result = ToolResult(
            intent="compound", 
            success=True, 
            message="\n---\n".join(messages), 
            code="\n\n".join(codes),
            sub_results=sub_results
        )
    else:
        result = dispatch_intent(
            intent_name=intent_name,
            entities=entities,
            confirmed=True,
        )

    # Note: we do NOT log the confirmation outcome to history to keep history clean, 
    # but you could easily log session.add_assistant_turn here if desired.

    _pending_result = None
    _pending_intent_data = None
    _pending_session_id = None

    return _build_response_payload(result, "", json.dumps(entities), session)


@app.post("/api/cancel")
async def api_cancel(session_id: str = Form("")):
    global _pending_result, _pending_intent_data, _pending_session_id
    _pending_result = None
    _pending_intent_data = None
    _pending_session_id = None
    
    session = session_manager.get_or_create(session_id if session_id else None)
    return {"status": "cancelled", "history": session.get_history()}


@app.post("/api/clear")
async def api_clear(session_id: str = Form("")):
    global _pending_result, _pending_intent_data, _pending_session_id
    _pending_result = None
    _pending_intent_data = None
    _pending_session_id = None
    
    if session_id and session_id in session_manager.sessions:
        session_manager.sessions[session_id].clear()
        session_manager.save()
        
    return {"status": "cleared", "history": []}

@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    session_manager.delete_session(session_id)
    return {"status": "deleted"}

@app.get("/api/sessions")
async def api_get_sessions():
    return {
        "sessions": session_manager.get_all_sessions_meta()
    }

@app.get("/api/history/{session_id}")
async def api_get_history(session_id: str):
    if session_id in session_manager.sessions:
        return {"session_id": session_id, "history": session_manager.sessions[session_id].get_history()}
    return {"session_id": session_id, "history": []}


@app.get("/api/status")
async def api_status():
    return {
        "whisper_ok": is_whisper_available(),
        "ollama_ok": is_ollama_available(),
    }


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)