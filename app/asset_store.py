import hashlib
import base64
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageFont

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
        deck_preview_images = deck_json.get("preview_images")
        if isinstance(deck_preview_images, list):
            preview_images = [image for image in deck_preview_images if isinstance(image, dict)]

    if not preview_images:
        return deck_json, []

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
            if source_url.startswith("data:image/"):
                stored_url = _store_data_url_asset(source_url, report_id, slide_index)
            else:
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


def ensure_slide_images(deck_json: dict[str, Any], report_id: str) -> dict[str, Any]:
    slides = deck_json.get("slides")
    if not isinstance(slides, list):
        return deck_json

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

    next_slides: list[Any] = []
    next_preview_images = list(preview_images) if isinstance(preview_images, list) else []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            next_slides.append(slide)
            continue
        image_url = str(
            slide.get("image_url")
            or slide.get("preview_image_url")
            or preview_image_by_index.get(index)
            or ""
        ).strip()
        if not image_url:
            image_url = _render_slide_image(slide, report_id, index)
            next_preview_images.append(
                {
                    "slide_index": index,
                    "label": str(slide.get("title") or slide.get("project_name") or f"Slide {index + 1}"),
                    "url": image_url,
                    "generated": True,
                }
            )
        next_slides.append({**slide, "image_url": image_url})

    return {**deck_json, "slides": next_slides, "preview_images": next_preview_images}


def _render_slide_image(slide: dict[str, Any], report_id: str, slide_index: int) -> str:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(image)
    title_font = _font(54)
    body_font = _font(34)
    small_font = _font(26)

    slide_type = str(slide.get("slide_type") or "slide").upper()
    title = str(slide.get("title") or slide.get("project_name") or f"Slide {slide_index + 1}")
    notes = str(slide.get("speaker_notes") or "")
    body_lines = _slide_body_lines(slide)

    draw.rectangle((0, 0, width, 18), fill="#6366f1")
    draw.text((72, 58), slide_type, fill="#64748b", font=small_font)
    draw.text((72, 112), title[:42], fill="#0f172a", font=title_font)

    y = 218
    for line in body_lines[:8]:
        for wrapped in _wrap(line, 46)[:2]:
            draw.text((92, y), "- " + wrapped, fill="#334155", font=body_font)
            y += 48
        y += 8

    if notes:
        draw.rounded_rectangle((72, 580, 1208, 664), radius=18, fill="#eef2ff")
        draw.text((98, 604), _wrap(notes, 58)[0], fill="#3730a3", font=small_font)

    relative_path = Path("previews") / report_id / f"slide_{slide_index}_generated.png"
    absolute_path = Path(get_settings().asset_storage_dir) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(absolute_path, format="PNG")
    return public_asset_url("assets/" + relative_path.as_posix())


def _slide_body_lines(slide: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("progress", "next_week_plan"):
        value = slide.get(key)
        if isinstance(value, list):
            lines.extend(str(item) for item in value if str(item).strip())
    for key in ("purpose", "owner", "category"):
        value = str(slide.get(key) or "").strip()
        if value:
            lines.append(value)
    if not lines:
        notes = str(slide.get("speaker_notes") or "").strip()
        if notes:
            lines.append(notes)
    return lines


def _wrap(text: str, limit: int) -> list[str]:
    clean = " ".join(str(text).split())
    if not clean:
        return []
    return [clean[index : index + limit] for index in range(0, len(clean), limit)]


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


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


def _store_data_url_asset(source_url: str, report_id: str, slide_index: int) -> str:
    header, separator, encoded = source_url.partition(",")
    if not separator or ";base64" not in header:
        raise ValueError("Preview image data URL must be base64 encoded.")

    content_type = header.removeprefix("data:").split(";")[0].strip()
    suffix = mimetypes.guess_extension(content_type) or ".png"
    content = base64.b64decode(encoded, validate=True)
    digest = hashlib.sha256(content).hexdigest()[:16]
    relative_path = Path("previews") / report_id / f"slide_{slide_index}_{digest}{suffix}"
    absolute_path = Path(get_settings().asset_storage_dir) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(content)
    return public_asset_url("assets/" + relative_path.as_posix())


def _suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return suffix
    return ""
