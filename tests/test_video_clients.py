import pytest

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
