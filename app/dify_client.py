import json
import re
from typing import Any

import httpx

from .config import get_settings

PREVIEW_START = "<ppt2video-preview-json>"
PREVIEW_END = "</ppt2video-preview-json>"


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
) -> dict[str, Any]:
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
        "conversation_id": "",
        "user": user_id,
    }
    headers = {"Authorization": "Bearer " + settings.dify_api_key}

    answer = await _stream_dify_answer(url, payload, headers)
    return extract_deck_json(answer)


async def _stream_dify_answer(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> str:
    chunks: list[str] = []
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                chunk = _parse_dify_stream_line(line)
                if chunk is None:
                    continue
                if chunk.get("event") == "error":
                    message = chunk.get("message") or chunk.get("code") or "Dify stream error"
                    raise RuntimeError(str(message))
                answer = chunk.get("answer")
                if isinstance(answer, str):
                    chunks.append(answer)
    return "".join(chunks)


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
) -> dict[str, Any]:
    query = (
        "請基於下面既有 deck_json 修改周報 PPT，不要重新生成一套無關內容。\n"
        "只根據用戶修改要求調整原有頁面的文案、項目內容與 speaker_notes；"
        "除非修改要求明確需要新增或刪除項目，否則保持原本頁數與結構。\n\n"
        "<current_deck_json>\n"
        f"{json.dumps(current_deck_json, ensure_ascii=False)}\n"
        "</current_deck_json>\n\n"
        "<revision_note>\n"
        f"{revision_note}\n"
        "</revision_note>"
    )
    return await create_deck_from_dify(
        reporter_name=reporter_name,
        report_period=report_period,
        report_date=report_date,
        raw_content=query,
        user_id=user_id,
    )
