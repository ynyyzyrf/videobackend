import pytest

from app.orchestrator import build_video_items


def test_build_video_items_preserves_slide_order():
    items = build_video_items(
        {
            "slides": [
                {
                    "slide_type": "cover",
                    "image_url": "https://example.com/cover.png",
                    "speaker_notes": "cover note",
                },
                {
                    "slide_type": "project",
                    "preview_image_url": "https://example.com/project.png",
                    "speaker_notes": "project note",
                },
            ]
        }
    )

    assert [item["slide_index"] for item in items] == [0, 1]
    assert items[1]["image_url"] == "https://example.com/project.png"


def test_build_video_items_rejects_long_notes():
    with pytest.raises(ValueError, match="exceeds 700"):
        build_video_items(
            {
                "slides": [
                    {
                        "slide_type": "cover",
                        "image_url": "https://example.com/cover.png",
                        "speaker_notes": "x" * 701,
                    }
                ]
            }
        )


def test_build_video_items_can_use_preview_images_by_slide_index():
    items = build_video_items(
        {
            "slides": [
                {"slide_type": "cover", "speaker_notes": "cover note"},
                {"slide_type": "summary", "speaker_notes": "summary note"},
            ],
            "preview_images": [
                {"slide_index": 1, "url": "https://example.com/summary.png"},
                {"slide_index": 0, "url": "https://example.com/cover.png"},
            ],
        }
    )

    assert [item["image_url"] for item in items] == [
        "https://example.com/cover.png",
        "https://example.com/summary.png",
    ]
