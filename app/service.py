from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.audio import probe_duration_seconds, write_audio_file
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
        force: bool,
        upload: bool,
        assets: AssetRequest | None = None,
        narration_style: str | None = None,
    ) -> dict[str, Any]:
        if audio_format != "mp3":
            raise ValueError("Apenas format=mp3 é suportado no MVP")

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
            bitrate=bitrate,
        )

        if not force and audio_path.exists() and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("input_hash") == input_hash:
                metadata["status"] = "completed"
                metadata["audio_path"] = str(audio_path)
                metadata["audio_url"] = self._audio_url(audio_path, upload)
                metadata["metadata_path"] = str(metadata_path)
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

        text_chunks = chunk_units(content.units, max_chars=self.settings.chunk_max_chars)
        audio_chunks = []
        chunk_metadata: list[dict[str, Any]] = []
        for index, chunk_text in enumerate(text_chunks, start=1):
            wav, sample_rate = self.tts.synthesize(
                chunk_text,
                language=selected_language,
                voice_clone_prompt=voice_clone_prompt,
            )
            audio_chunks.append((wav, sample_rate))
            chunk_metadata.append(
                {
                    "index": index,
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
            "audio_url": self._audio_url(audio_path, upload),
            "metadata_path": str(metadata_path),
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
                "bitrate": bitrate,
            }
        )

    def _audio_url(self, audio_path: Path, upload: bool) -> str | None:
        if not upload or not self.settings.public_base_url:
            return None
        relative = audio_path.relative_to(Path(self.settings.output_dir)).as_posix()
        return f"{self.settings.public_base_url.rstrip('/')}/outputs/{relative}"


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
