import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_settings

PREVIEW_START = "<ppt2video-preview-json>"
PREVIEW_END = "</ppt2video-preview-json>"


@dataclass(frozen=True)
class DifyDeckResult:
    deck_json: dict[str, Any]
    conversation_id: str


def extract_deck_json(answer: str) -> dict[str, Any]:
    start = answer.find(PREVIEW_START)
    if start >= 0:
        content_start = start + len(PREVIEW_START)
        end = answer.find(PREVIEW_END, content_start)
        if end >= 0:
            payload = json.loads(answer[content_start:end].strip())
            deck = payload.get("deck_json")
            if isinstance(deck, dict):
                return deck

    match = re.search(r"\{.*\}", answer, flags=re.S)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict) and isinstance(parsed.get("deck_json"), dict):
            return parsed["deck_json"]
        if isinstance(parsed, dict) and isinstance(parsed.get("slides"), list):
            return parsed

    raise ValueError("Dify response did not include deck_json.")


async def create_deck_from_dify(
    *,
    reporter_name: str,
    report_period: str,
    report_date: str,
    raw_content: str,
    user_id: str,
    conversation_id: str = "",
) -> DifyDeckResult:
    settings = get_settings()
    if not settings.dify_api_key:
        raise RuntimeError("DIFY_API_KEY is not configured.")

    url = settings.dify_api_base.rstrip("/") + "/chat-messages"
    payload = {
        "inputs": {
            "reporter_name": reporter_name,
            "report_period": report_period,
            "report_date": report_date,
        },
        "query": raw_content,
        "response_mode": "streaming",
        "conversation_id": conversation_id,
        "user": user_id,
    }
    headers = {"Authorization": "Bearer " + settings.dify_api_key}

    answer, response_conversation_id = await _stream_dify_answer(url, payload, headers)
    return DifyDeckResult(
        deck_json=extract_deck_json(answer),
        conversation_id=response_conversation_id or conversation_id,
    )


async def _stream_dify_answer(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> tuple[str, str]:
    chunks: list[str] = []
    conversation_id = ""
    events: list[str] = []
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if not response.is_success:
                body = (await response.aread()).decode("utf-8", errors="replace")
                body = body.strip().replace("\r", " ").replace("\n", " ")[:1000]
                raise RuntimeError(f"Dify HTTP {response.status_code}: {body or response.reason_phrase}")
            async for line in response.aiter_lines():
                chunk = _parse_dify_stream_line(line)
                if chunk is None:
                    continue
                if chunk.get("event") == "error":
                    details = [
                        f"{key}={chunk[key]}"
                        for key in ("status", "code", "message")
                        if chunk.get(key) not in (None, "")
                    ]
                    error = "Dify stream error" + (
                        ": " + ", ".join(details) if details else ""
                    )
                    if events:
                        error += "; events=" + " > ".join(events)
                    raise RuntimeError(error)
                event = chunk.get("event")
                if isinstance(event, str) and len(events) < 30:
                    data = chunk.get("data")
                    title = data.get("title") if isinstance(data, dict) else None
                    events.append(f"{event}({title})" if title else event)
                chunk_conversation_id = chunk.get("conversation_id")
                if isinstance(chunk_conversation_id, str) and chunk_conversation_id:
                    conversation_id = chunk_conversation_id
                answer = chunk.get("answer")
                if isinstance(answer, str):
                    chunks.append(answer)
    return "".join(chunks), conversation_id


def _parse_dify_stream_line(line: str) -> dict[str, Any] | None:
    value = line.strip()
    if not value or not value.startswith("data:"):
        return None
    raw = value[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def revise_deck_from_dify(
    *,
    reporter_name: str,
    report_period: str,
    report_date: str,
    current_deck_json: dict[str, Any],
    revision_note: str,
    user_id: str,
    conversation_id: str,
) -> DifyDeckResult:
    query = json.dumps(
        {
            "source": "ppt2video_frontend_editor",
            "version": 1,
            "action": "regenerate_preview",
            "deck_json": current_deck_json,
            "revision_note": revision_note,
        },
        ensure_ascii=False,
    )
    return await create_deck_from_dify(
        reporter_name=reporter_name,
        report_period=report_period,
        report_date=report_date,
        raw_content=query,
        user_id=user_id,
        conversation_id=conversation_id,
    )
