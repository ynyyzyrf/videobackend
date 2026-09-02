import asyncio

from app import asset_store


def test_persist_preview_assets_does_not_generate_missing_slide_images(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_store.get_settings(), "asset_storage_dir", str(tmp_path))
    monkeypatch.setattr(asset_store.get_settings(), "public_base_url", "https://backend.example")

    deck_json, preview_images = asyncio.run(
        asset_store.persist_preview_assets(
            {
                "slides": [
                    {
                        "slide_type": "cover",
                        "title": "項目週匯報",
                        "speaker_notes": "開場旁白",
                    }
                ]
            },
            [],
            "report_1",
        )
    )

    assert "image_url" not in deck_json["slides"][0]
    assert "preview_images" not in deck_json
    assert preview_images == []
    assert not (tmp_path / "previews" / "report_1" / "slide_0_generated.png").exists()


def test_ensure_slide_images_generates_video_fallback_images(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_store.get_settings(), "asset_storage_dir", str(tmp_path))
    monkeypatch.setattr(asset_store.get_settings(), "public_base_url", "https://backend.example")

    deck_json = asset_store.ensure_slide_images(
        {
            "slides": [
                {
                    "slide_type": "cover",
                    "title": "項目週匯報",
                    "speaker_notes": "開場旁白",
                }
            ]
        },
        "report_1",
    )

    image_url = deck_json["slides"][0]["image_url"]
    assert image_url == "https://backend.example/assets/previews/report_1/slide_0_generated.png"
    assert deck_json["preview_images"][0]["generated"] is True
    assert (tmp_path / "previews" / "report_1" / "slide_0_generated.png").exists()


def test_persist_preview_assets_uses_deck_preview_images(monkeypatch):
    async def fake_download_asset(_client, source_url, report_id, slide_index):
        return f"https://backend.example/assets/{report_id}/{slide_index}.png?from={source_url}"

    monkeypatch.setattr(asset_store, "_download_asset", fake_download_asset)

    deck_json, preview_images = asyncio.run(
        asset_store.persist_preview_assets(
            {
                "slides": [{"slide_type": "cover", "speaker_notes": "開場旁白"}],
                "preview_images": [{"slide_index": 0, "url": "https://dify.example/preview.png"}],
            },
            [],
            "report_1",
        )
    )

    assert deck_json["slides"][0]["image_url"].startswith(
        "https://backend.example/assets/report_1/0.png"
    )
    assert preview_images[0]["source_url"] == "https://dify.example/preview.png"
