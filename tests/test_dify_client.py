import asyncio
import json

import httpx
import pytest

from app import dify_client


def test_create_deck_from_dify_uses_streaming_and_parses_sse(monkeypatch):
    monkeypatch.setattr(dify_client.get_settings(), "dify_api_key", "test-key")
    monkeypatch.setattr(dify_client.get_settings(), "dify_api_base", "https://dify.example/v1")
    requests: list[dict[str, object]] = []
    original_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        requests.append(payload)
        body = "\n\n".join(
            [
                'data: {"event":"message","answer":"<ppt2video-preview-json>{\\"deck_json\\":{\\"slides\\":["}',
                'data: {"event":"message","answer":"{\\"slide_type\\":\\"cover\\",\\"speaker_notes\\":\\"ok\\"}" }',
                'data: {"event":"message","answer":"]}}</ppt2video-preview-json>"}',
                'data: {"event":"message_end","conversation_id":"conv-123"}',
            ]
        )
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, exc_type, exc, tb):
            await self.client.aclose()

    monkeypatch.setattr(dify_client.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        dify_client.create_deck_from_dify(
            reporter_name="MAG",
            report_period="2026/08/24 - 2026/08/30",
            report_date="2026/08/27",
            raw_content="我是mag",
            user_id="report_1",
        )
    )

    assert requests[0]["response_mode"] == "streaming"
    assert result.deck_json["slides"][0]["speaker_notes"] == "ok"
    assert result.conversation_id == "conv-123"


def test_extract_deck_json_preserves_marker_preview_images():
    answer = (
        "<ppt2video-preview-json>"
        + json.dumps(
            {
                "deck_json": {
                    "slides": [
                        {"slide_type": "cover", "title": "項目週匯報"},
                        {"slide_type": "summary", "title": "總結"},
                    ]
                },
                "preview_images": [
                    {"slide_index": 0, "label": "封面", "url": "https://dify.example/cover.png"},
                    {
                        "slide_index": 1,
                        "label": "總結頁",
                        "url": "https://dify.example/summary.png",
                    },
                ],
            },
            ensure_ascii=False,
        )
        + "</ppt2video-preview-json>"
    )

    deck_json = dify_client.extract_deck_json(answer)

    assert deck_json["preview_images"][0]["url"] == "https://dify.example/cover.png"
    assert deck_json["slides"][0]["image_url"] == "https://dify.example/cover.png"
    assert deck_json["slides"][1]["preview_image_url"] == "https://dify.example/summary.png"


def test_revise_deck_reuses_conversation_and_sends_editor_payload(monkeypatch):
    captured: list[dict[str, object]] = []

    async def fake_create_deck_from_dify(**kwargs):
        captured.append(kwargs)
        return dify_client.DifyDeckResult(deck_json={"slides": []}, conversation_id="conv-123")

    monkeypatch.setattr(dify_client, "create_deck_from_dify", fake_create_deck_from_dify)

    asyncio.run(
        dify_client.revise_deck_from_dify(
            reporter_name="MAG",
            report_period="2026/08/24 - 2026/08/30",
            report_date="2026/08/27",
            current_deck_json={"slides": [{"title": "old"}]},
            revision_note="改得更正式",
            user_id="user-123",
            conversation_id="conv-123",
        )
    )

    request = captured[0]
    query = json.loads(str(request["raw_content"]))
    assert request["conversation_id"] == "conv-123"
    assert query["source"] == "ppt2video_frontend_editor"
    assert query["action"] == "regenerate_preview"
    assert query["deck_json"]["slides"][0]["title"] == "old"


def test_stream_dify_answer_includes_error_event_details(monkeypatch):
    original_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        body = "\n\n".join(
            [
                'data: {"event":"workflow_started"}',
                'data: {"event":"node_started","data":{"title":"02_文字結構化"}}',
                'data: {"event":"error","status":500,"code":"provider_error",'
                '"message":"Model provider failed"}',
            ]
        )
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, exc_type, exc, tb):
            await self.client.aclose()

    monkeypatch.setattr(dify_client.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(
        RuntimeError,
        match=(
            "Dify stream error: status=500, code=provider_error, "
            "message=Model provider failed; events=workflow_started > "
            "node_started\\(02_文字結構化\\)"
        ),
    ):
        asyncio.run(
            dify_client._stream_dify_answer(
                "https://dify.example/v1/chat-messages",
                {"query": "hello"},
                {"Authorization": "Bearer test-key"},
            )
        )


def test_stream_dify_answer_includes_http_error_body(monkeypatch):
    original_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"code": "internal_server_error", "message": "workflow failed"},
            request=request,
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, exc_type, exc, tb):
            await self.client.aclose()

    monkeypatch.setattr(dify_client.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(
        RuntimeError,
        match='Dify HTTP 500: {"code":"internal_server_error","message":"workflow failed"}',
    ):
        asyncio.run(
            dify_client._stream_dify_answer(
                "https://dify.example/v1/chat-messages",
                {"query": "hello"},
                {"Authorization": "Bearer test-key"},
            )
        )
