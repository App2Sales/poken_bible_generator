from __future__ import annotations

import logging
from typing import Any

from app.assets import AssetRequest
from app.config import settings
from app.service import GenerationService

logging.basicConfig(level=logging.INFO)

_service: GenerationService | None = None


def get_service() -> GenerationService:
    global _service
    if _service is None:
        _service = GenerationService(settings)
        _service.startup()
    return _service


def handler(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("input") or {}
    service = get_service()
    return service.generate_chapter(
        book=payload.get("book", "Salmos"),
        chapter=int(payload["chapter"]),
        voice_id=payload.get("voice_id"),
        language=payload.get("language"),
        audio_format=payload.get("format", "mp3"),
        bitrate=payload.get("bitrate", "192k"),
        include_headings=parse_bool(payload.get("include_headings", False)),
        include_verse_numbers=parse_bool(payload.get("include_verse_numbers", False)),
        include_chapter_intro=parse_bool(payload.get("include_chapter_intro", True)),
        chapter_intro_pause_seconds=parse_optional_float(payload.get("chapter_intro_pause_seconds")),
        force=parse_bool(payload.get("force", False)),
        upload=parse_bool(payload.get("upload", True)),
        assets=asset_request(payload.get("assets")),
        narration_style=payload.get("narration_style"),
    )


def asset_request(value: Any) -> AssetRequest | None:
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("assets deve ser um objeto com bible_db_url, ref_audio_url e/ou ref_text_url")
    return AssetRequest(
        bible_db_url=value.get("bible_db_url"),
        ref_audio_url=value.get("ref_audio_url"),
        ref_text_url=value.get("ref_text_url"),
    )


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
