import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from . import db
from .config import get_settings
from .video_clients import concat_videos, get_video_task, submit_video_task


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_video_items(deck_json: dict[str, Any]) -> list[dict[str, Any]]:
    slides = deck_json.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("deck_json.slides is required.")

    preview_image_by_index: dict[int, str] = {}
    preview_images = deck_json.get("preview_images")
    if isinstance(preview_images, list):
        for image in preview_images:
            if not isinstance(image, dict):
                continue
            try:
                slide_index = int(image.get("slide_index"))
            except Exception:
                continue
            url = str(image.get("url") or "").strip()
            if url:
                preview_image_by_index[slide_index] = url

    items: list[dict[str, Any]] = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise ValueError(f"Slide {index + 1} is invalid.")
        image_url = str(
            slide.get("image_url")
            or slide.get("preview_image_url")
            or preview_image_by_index.get(index)
            or ""
        ).strip()
        text_content = str(slide.get("speaker_notes") or "").strip()
        if not image_url:
            raise ValueError(f"Slide {index + 1} missing image_url.")
        if not text_content:
            raise ValueError(f"Slide {index + 1} missing speaker_notes.")
        if len(text_content) > 700:
            raise ValueError(f"Slide {index + 1} speaker_notes exceeds 700 characters.")
        items.append(
            {
                "slide_index": index,
                "slide_type": str(slide.get("slide_type") or "unknown"),
                "image_url": image_url,
                "text_content": text_content,
            }
        )
    return items


def create_video_job(report_id: str, version_id: str, deck_json: dict[str, Any]) -> str:
    items = build_video_items(deck_json)
    job_id = "job_" + uuid.uuid4().hex
    now = utc_now()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO video_jobs
                (id, report_id, report_version_id, status, progress, total, completed, created_at, updated_at)
                VALUES (%s, %s, %s, 'queued', 0, %s, 0, %s, %s)
                """,
                (job_id, report_id, version_id, len(items), now, now),
            )
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO video_job_items
                    (id, job_id, slide_index, slide_type, image_url, text_content, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s)
                    """,
                    (
                        "item_" + uuid.uuid4().hex,
                        job_id,
                        item["slide_index"],
                        item["slide_type"],
                        item["image_url"],
                        item["text_content"],
                        now,
                        now,
                    ),
                )
    return job_id


async def run_video_job(job_id: str) -> None:
    try:
        await _run_video_job(job_id)
    except Exception as exc:
        _mark_job_failed(job_id, str(exc))


async def _run_video_job(job_id: str) -> None:
    _update_job(job_id, status="processing")
    for item in _job_items(job_id):
        if item["status"] == "success":
            continue
        await _process_item(dict(item))
        _refresh_job_progress(job_id)

    items = _job_items(job_id)
    video_urls = [
        str(item["video_url"])
        for item in sorted(items, key=lambda row: int(row["slide_index"]))
    ]
    final_url = await concat_videos(video_urls)
    _update_job(job_id, status="completed", progress=100, final_video_url=final_url, error=None)


async def _process_item(item: dict[str, Any]) -> None:
    settings = get_settings()
    item_id = str(item["id"])
    deadline = asyncio.get_running_loop().time() + settings.video_task_timeout_seconds
    attempts = int(item["attempts"] or 0)

    while attempts <= settings.video_task_max_retries:
        attempts += 1
        try:
            task_id = await submit_video_task(str(item["image_url"]), str(item["text_content"]))
            _update_item(item_id, status="processing", task_id=task_id, attempts=attempts, error=None)
            video_url = await _poll_task(task_id, deadline)
            _update_item(item_id, status="success", video_url=video_url, error=None)
            return
        except Exception as exc:
            _update_item(item_id, status="failed", attempts=attempts, error=str(exc))
            if attempts > settings.video_task_max_retries:
                raise


async def _poll_task(task_id: str, deadline: float) -> str:
    settings = get_settings()
    while asyncio.get_running_loop().time() < deadline:
        task = await get_video_task(task_id)
        status = int(task.get("status") or 0)
        if status == 40:
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            video_url = str(result.get("videoUrl") or "").strip()
            if not video_url:
                raise RuntimeError("Video task succeeded without videoUrl.")
            return video_url
        if status == 80:
            raise RuntimeError(str(task.get("error") or "Video task failed."))
        if status not in (10, 20):
            raise RuntimeError(f"Unknown video task status: {status}")
        await asyncio.sleep(settings.video_task_poll_interval_seconds)
    raise TimeoutError("Video task timed out.")


def _job_items(job_id: str) -> list[dict[str, Any]]:
    return db.fetchall(
        "SELECT * FROM video_job_items WHERE job_id = %s ORDER BY slide_index",
        (job_id,),
    )


def _update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    db.execute(
        f"UPDATE video_jobs SET {assignments} WHERE id = %s",
        (*fields.values(), job_id),
    )


def _update_item(item_id: str, **fields: Any) -> None:
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    db.execute(
        f"UPDATE video_job_items SET {assignments} WHERE id = %s",
        (*fields.values(), item_id),
    )


def _refresh_job_progress(job_id: str) -> None:
    row = db.fetchone(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS completed
        FROM video_job_items
        WHERE job_id = %s
        """,
        (job_id,),
    )
    total = int((row or {}).get("total") or 0)
    completed = int((row or {}).get("completed") or 0)
    progress = int((completed / total) * 95) if total else 0
    db.execute(
        """
        UPDATE video_jobs
        SET total = %s, completed = %s, progress = %s, updated_at = %s
        WHERE id = %s
        """,
        (total, completed, progress, utc_now(), job_id),
    )


def _mark_job_failed(job_id: str, error: str) -> None:
    _update_job(job_id, status="failed", error=error)
