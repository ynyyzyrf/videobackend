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
        "response_mode": "blocking",
        "conversation_id": "",
        "user": user_id,
    }
    headers = {"Authorization": "Bearer " + settings.dify_api_key}

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    answer = str(data.get("answer") or "")
    return extract_deck_json(answer)
