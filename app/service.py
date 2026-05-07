from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.audio import probe_duration_seconds, silence, write_audio_file
from app.assets import AssetManager, AssetRequest, ResolvedAssets
from app.bible import BibleRepository
from app.config import Settings
from app.tts_engine import TTSEngine
from app.utils import file_sha256, slugify, stable_hash

logger = logging.getLogger(__name__)


class GenerationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.assets = AssetManager(settings)
        self.tts = TTSEngine(
            settings.model_id,
            settings.tts_mode,
            voice_id=settings.voice_id,
            x_vector_only_mode=settings.x_vector_only_mode,
        )

    def startup(self) -> None:
        if self.settings.x_vector_only_mode:
            logger.warning(
                "X_VECTOR_ONLY_MODE=true está ativo. Use apenas para teste; em produção use X_VECTOR_ONLY_MODE=false."
            )
        self.tts.load_model()

    def voice_info(self) -> dict[str, Any]:
        info = self.tts.voice_info()
        if info.get("ref_audio_path_exists") or info.get("ref_text_path_exists"):
            return info

        ref_audio_path = Path(self.settings.ref_audio_path)
        ref_text_path = Path(self.settings.ref_text_path)
        ref_text_sha256 = None
        if ref_text_path.exists():
            ref_text_sha256 = file_sha256(ref_text_path)

        return {
            **info,
            "voice_id": self.settings.voice_id,
            "ref_audio_path_exists": ref_audio_path.exists(),
            "ref_text_path_exists": ref_text_path.exists(),
            "ref_audio_sha256": file_sha256(ref_audio_path) if ref_audio_path.exists() else None,
            "ref_text_sha256": ref_text_sha256,
            "x_vector_only_mode": self.settings.x_vector_only_mode,
        }

    def generate_chapter(
        self,
        *,
        book: str,
        chapter: int,
        voice_id: str | None,
        language: str | None,
        audio_format: str,
        bitrate: str,
        include_headings: bool,
        include_verse_numbers: bool,
        include_chapter_intro: bool,
        chapter_intro_pause_seconds: float | None,
        force: bool,
        upload: bool,
        assets: AssetRequest | None = None,
        narration_style: str | None = None,
    ) -> dict[str, Any]:
        if audio_format != "mp3":
            raise ValueError("Apenas format=mp3 é suportado no MVP")

        pause_seconds = (
            chapter_intro_pause_seconds
            if chapter_intro_pause_seconds is not None
            else self.settings.chapter_intro_pause_seconds
        )
        if pause_seconds < 0 or pause_seconds > 10:
            raise ValueError("chapter_intro_pause_seconds deve estar entre 0 e 10")
        if not include_chapter_intro:
            pause_seconds = 0

        requested_voice_id = voice_id or self.settings.voice_id

        if narration_style:
            logger.info(
                "narration_style recebido, mas voice_clone mantém a voz do áudio de referência: %s",
                narration_style,
            )

        selected_language = language or self.settings.default_language
        resolved_assets = self.assets.resolve(
            assets,
            x_vector_only_mode=self.settings.x_vector_only_mode,
            force_download=force,
        )
        bible = BibleRepository(resolved_assets.bible_db_path)
        bible.validate()

        content = bible.get_chapter(
            book,
            chapter,
            include_headings=include_headings,
            include_verse_numbers=include_verse_numbers,
            include_chapter_intro=include_chapter_intro,
        )
        book_slug = slugify(content.book)
        chapter_name = f"{book_slug}_{chapter:03d}"
        output_root = Path(self.settings.output_dir) / "default" / book_slug
        audio_path = output_root / f"{chapter_name}.mp3"
        metadata_path = output_root / "metadata" / f"{chapter_name}.json"

        input_hash = self._input_hash(
            book_id=content.book_id,
            chapter=chapter,
            full_text=content.text,
            voice_id=requested_voice_id,
            resolved_assets=resolved_assets,
            language=selected_language,
            include_headings=include_headings,
            include_verse_numbers=include_verse_numbers,
            include_chapter_intro=include_chapter_intro,
            chapter_intro_pause_seconds=pause_seconds,
            bitrate=bitrate,
        )

        if not force and audio_path.exists() and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("input_hash") == input_hash:
                metadata["status"] = "completed"
                metadata["audio_path"] = str(audio_path)
                metadata["audio_url"] = self._api_url(f"/download/{book_slug}/{chapter}")
                metadata["metadata_path"] = str(metadata_path)
                metadata["metadata_url"] = self._api_url(f"/download/{book_slug}/{chapter}/metadata")
                return metadata

        voice_clone_prompt = self.tts.get_voice_clone_prompt(
            voice_id=requested_voice_id,
            ref_audio_path=resolved_assets.ref_audio_path,
            ref_text_path=resolved_assets.ref_text_path,
            ref_text=resolved_assets.ref_text,
            ref_audio_sha256=resolved_assets.ref_audio_sha256,
            ref_text_sha256=resolved_assets.ref_text_sha256,
            x_vector_only_mode=self.settings.x_vector_only_mode,
        )

        audio_chunks = []
        chunk_metadata: list[dict[str, Any]] = []
        intro_units, body_units = split_intro_units(content.units, include_chapter_intro)

        for intro_text in intro_units:
            wav, sample_rate = self.tts.synthesize(
                intro_text,
                language=selected_language,
                voice_clone_prompt=voice_clone_prompt,
            )
            audio_chunks.append((wav, sample_rate))
            chunk_metadata.append(
                {
                    "index": len(chunk_metadata) + 1,
                    "type": "chapter_intro",
                    "text_chars": len(intro_text),
                    "sample_rate": sample_rate,
                }
            )
            if pause_seconds > 0:
                audio_chunks.append((silence(pause_seconds, sample_rate), sample_rate))
                chunk_metadata.append(
                    {
                        "index": len(chunk_metadata) + 1,
                        "type": "silence",
                        "duration_seconds": pause_seconds,
                        "sample_rate": sample_rate,
                    }
                )

        text_chunks = chunk_units(body_units, max_chars=self.settings.chunk_max_chars)
        for chunk_text in text_chunks:
            wav, sample_rate = self.tts.synthesize(
                chunk_text,
                language=selected_language,
                voice_clone_prompt=voice_clone_prompt,
            )
            audio_chunks.append((wav, sample_rate))
            chunk_metadata.append(
                {
                    "index": len(chunk_metadata) + 1,
                    "type": "text",
                    "text_chars": len(chunk_text),
                    "sample_rate": sample_rate,
                }
            )

        fallback_duration = write_audio_file(audio_chunks, audio_path, bitrate)
        duration_seconds = round(probe_duration_seconds(audio_path, fallback_duration), 2)
        audio_sha256 = file_sha256(audio_path)

        metadata = {
            "book_id": content.book_id,
            "book": content.book,
            "chapter": chapter,
            "voice_id": requested_voice_id,
            "model_id": self.settings.model_id,
            "tts_mode": self.settings.tts_mode,
            "bible_db_sha256": resolved_assets.bible_db_sha256,
            "ref_audio_sha256": resolved_assets.ref_audio_sha256,
            "ref_text_sha256": resolved_assets.ref_text_sha256,
            "asset_urls": {
                "bible_db_url": resolved_assets.bible_db_url,
                "ref_audio_url": resolved_assets.ref_audio_url,
                "ref_text_url": resolved_assets.ref_text_url,
            },
            "language": selected_language,
            "include_headings": include_headings,
            "include_verse_numbers": include_verse_numbers,
            "include_chapter_intro": include_chapter_intro,
            "chapter_intro_pause_seconds": pause_seconds,
            "chunks": chunk_metadata,
            "duration_seconds": duration_seconds,
            "sha256": audio_sha256,
            "input_hash": input_hash,
        }
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "status": "completed",
            **metadata,
            "audio_path": str(audio_path),
            "audio_url": self._api_url(f"/download/{book_slug}/{chapter}"),
            "metadata_path": str(metadata_path),
            "metadata_url": self._api_url(f"/download/{book_slug}/{chapter}/metadata"),
        }

    def _input_hash(
        self,
        *,
        book_id: int,
        chapter: int,
        full_text: str,
        voice_id: str,
        resolved_assets: ResolvedAssets,
        language: str,
        include_headings: bool,
        include_verse_numbers: bool,
        include_chapter_intro: bool,
        chapter_intro_pause_seconds: float,
        bitrate: str,
    ) -> str:
        return stable_hash(
            {
                "book_id": book_id,
                "chapter": chapter,
                "full_text": full_text,
                "model_id": self.settings.model_id,
                "tts_mode": self.settings.tts_mode,
                "voice_id": voice_id,
                "bible_db_sha256": resolved_assets.bible_db_sha256,
                "ref_audio_sha256": resolved_assets.ref_audio_sha256,
                "ref_text_sha256": resolved_assets.ref_text_sha256,
                "language": language,
                "include_headings": include_headings,
                "include_verse_numbers": include_verse_numbers,
                "include_chapter_intro": include_chapter_intro,
                "chapter_intro_pause_seconds": chapter_intro_pause_seconds,
                "bitrate": bitrate,
            }
        )

    def _api_url(self, path: str) -> str:
        if self.settings.public_base_url:
            return f"{self.settings.public_base_url.rstrip('/')}{path}"
        return path


def chunk_units(units: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        projected = current_len + len(unit) + (1 if current else 0)
        if current and projected > max_chars:
            chunks.append(" ".join(current))
            current = [unit]
            current_len = len(unit)
        else:
            current.append(unit)
            current_len = projected

    if current:
        chunks.append(" ".join(current))

    return chunks


def split_intro_units(units: list[str], include_chapter_intro: bool) -> tuple[list[str], list[str]]:
    if include_chapter_intro and units:
        return [units[0]], units[1:]
    return [], units
