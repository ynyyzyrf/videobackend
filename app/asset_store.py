import hashlib
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import get_settings


def public_asset_url(asset_path: str) -> str:
    base_url = get_settings().public_base_url.rstrip("/")
    if not base_url:
        return "/" + asset_path.lstrip("/")
    return base_url + "/" + asset_path.lstrip("/")


async def persist_preview_assets(
    deck_json: dict[str, Any],
    preview_images: list[dict[str, Any]],
    report_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not preview_images:
        return deck_json, preview_images

    persisted_images: list[dict[str, Any]] = []
    url_by_index: dict[int, str] = {}

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for image in preview_images:
            if not isinstance(image, dict):
                continue
            try:
                slide_index = int(image.get("slide_index"))
            except Exception:
                continue
            source_url = str(image.get("url") or "").strip()
            if not source_url:
                continue
            stored_url = await _download_asset(client, source_url, report_id, slide_index)
            next_image = {**image, "source_url": source_url, "url": stored_url}
            persisted_images.append(next_image)
            url_by_index[slide_index] = stored_url

    if not url_by_index:
        return deck_json, preview_images

    slides = deck_json.get("slides")
    if isinstance(slides, list):
        next_slides = []
        for index, slide in enumerate(slides):
            if isinstance(slide, dict) and index in url_by_index:
                next_slides.append({**slide, "image_url": url_by_index[index]})
            else:
                next_slides.append(slide)
        deck_json = {**deck_json, "slides": next_slides}

    return {**deck_json, "preview_images": persisted_images}, persisted_images


async def _download_asset(
    client: httpx.AsyncClient,
    source_url: str,
    report_id: str,
    slide_index: int,
) -> str:
    response = await client.get(source_url)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    suffix = mimetypes.guess_extension(content_type) or _suffix_from_url(source_url) or ".bin"
    digest = hashlib.sha256(response.content).hexdigest()[:16]
    relative_path = Path("previews") / report_id / f"slide_{slide_index}_{digest}{suffix}"
    absolute_path = Path(get_settings().asset_storage_dir) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(response.content)

    return public_asset_url("assets/" + relative_path.as_posix())


def _suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return suffix
    return ""
