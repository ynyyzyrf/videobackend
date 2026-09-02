import json
from typing import Any

import httpx

from .config import get_settings


async def submit_video_task(image_url: str, text_content: str) -> str:
    settings = get_settings()
    url = settings.video_composer_base_url.rstrip("/") + "/api/video/generate/cloud/tasks"
    headers = _video_composer_headers(content_type=True)

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            url,
            headers=headers,
            json={"imageUrl": image_url, "textContent": text_content},
        )
        response.raise_for_status()
        data = response.json()

    if int(data.get("code") or 0) != 200:
        raise RuntimeError(str(data.get("msg") or "Video Composer submit failed."))
    task_id = data.get("data")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError("Video Composer did not return taskId.")
    return task_id


async def get_video_task(task_id: str) -> dict[str, Any]:
    settings = get_settings()
    url = settings.video_composer_base_url.rstrip() + "/api/video/generate/cloud/tasks"
    headers = _video_composer_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params={"taskId": task_id}, headers=headers)
        response.raise_for_status()
        data = response.json()

    if int(data.get("code") or 0) != 200:
        raise RuntimeError(str(data.get("msg") or "Video Composer status failed."))
    task = data.get("data")
    if not isinstance(task, dict):
        raise RuntimeError("Video Composer status response missing data.")
    return task


def _video_composer_headers(content_type: bool = False) -> dict[str, str]:
    settings = get_settings()
    if not settings.video_composer_api_key:
        raise RuntimeError("VIDEO_COMPOSER_API_KEY is not configured.")

    headers = {"X-API-Key": settings.video_composer_api_key}
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


async def concat_videos(video_urls: list[str]) -> str:
    settings = get_settings()
    if not settings.ffmpeg_api_key:
        raise RuntimeError("FFMPEG_API_KEY is not configured.")

    input_files = {f"input_{index}.mp4": url for index, url in enumerate(video_urls)}
    input_args = " ".join(f"-i {{{{input_{index}.mp4}}}}" for index in range(len(video_urls)))
    concat_inputs = "".join(f"[{index}:v:0][{index}:a:0]" for index in range(len(video_urls)))
    command = (
        input_args
        + f' -filter_complex "{concat_inputs}concat=n={len(video_urls)}:v=1:a=1[v][a]"'
        + ' -map "[v]" -map "[a]" -c:v libx264 -preset veryfast -crf 23'
        + " -c:a aac -b:a 128k -movflags +faststart {{output.mp4}}"
    )
    payload = {
        "input_files": input_files,
        "output_files": ["output.mp4"],
        "ffmpeg_commands": [command],
    }
    headers = _ffmpeg_headers()

    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(settings.ffmpeg_api_url, headers=headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise RuntimeError("FFMPEG_API_KEY is invalid or unauthorized.") from exc
            raise
        data = response.json()

    return _extract_output_url(data)


def _ffmpeg_headers() -> dict[str, str]:
    settings = get_settings()
    token = _normalize_bearer_token(settings.ffmpeg_api_key, "FFMPEG_API_KEY")
    return {
        "Authorization": token,
        "Content-Type": "application/json",
    }


def _normalize_bearer_token(value: str, name: str) -> str:
    token = value.strip()
    if not token:
        raise RuntimeError(f"{name} is not configured.")

    lower = token.lower()
    if lower.startswith("authorization:"):
        token = token.split(":", 1)[1].strip()
        lower = token.lower()
    if lower.startswith("bearer "):
        return "Bearer " + token[7:].strip()
    if lower.startswith("bearer"):
        return "Bearer " + token[6:].lstrip(":").strip()
    return "Bearer " + token


def _extract_output_url(data: Any) -> str:
    root = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if isinstance(root, dict):
        outputs = root.get("output_files")
        if isinstance(outputs, dict):
            url = outputs.get("output.mp4") or outputs.get("output")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
        if isinstance(outputs, list):
            for item in outputs:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return item
                if isinstance(item, dict):
                    url = item.get("url") or item.get("download_url")
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        return url

    text = json.dumps(data)
    marker = "https://"
    start = text.find(marker)
    while start >= 0:
        end = min([i for i in [text.find('"', start), text.find("\\", start)] if i >= 0] or [len(text)])
        candidate = text[start:end]
        if "output.mp4" in candidate:
            return candidate
        start = text.find(marker, start + 1)
    raise RuntimeError("FFmpeg response did not include output.mp4 URL.")
