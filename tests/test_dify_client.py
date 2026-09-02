import asyncio
import json

import httpx

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
                'data: {"event":"message_end"}',
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

    deck_json = asyncio.run(
        dify_client.create_deck_from_dify(
            reporter_name="MAG",
            report_period="2026/08/24 - 2026/08/30",
            report_date="2026/08/27",
            raw_content="我是mag",
            user_id="report_1",
        )
    )

    assert requests[0]["response_mode"] == "streaming"
    assert deck_json["slides"][0]["speaker_notes"] == "ok"
