import asyncio

import pytest
import httpx

from app import video_clients


def test_video_composer_headers_use_x_api_key(monkeypatch):
    monkeypatch.setattr(video_clients.get_settings(), "video_composer_api_key", "test-key")

    headers = video_clients._video_composer_headers(content_type=True)

    assert headers == {
        "X-API-Key": "test-key",
        "Content-Type": "application/json",
    }
    assert "Authorization" not in headers


def test_video_composer_headers_require_api_key(monkeypatch):
    monkeypatch.setattr(video_clients.get_settings(), "video_composer_api_key", "")

    with pytest.raises(RuntimeError, match="VIDEO_COMPOSER_API_KEY"):
        video_clients._video_composer_headers()


def test_concat_videos_reports_invalid_ffmpeg_key(monkeypatch):
    monkeypatch.setattr(video_clients.get_settings(), "ffmpeg_api_key", "bad-key")
    original_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, exc_type, exc, tb):
            await self.client.aclose()

    monkeypatch.setattr(video_clients.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(RuntimeError, match="FFMPEG_API_KEY is invalid"):
        asyncio.run(video_clients.concat_videos(["https://example.com/1.mp4"]))
