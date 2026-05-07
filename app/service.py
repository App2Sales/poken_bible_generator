from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.audio import probe_duration_seconds, silence, write_audio_file
from app.assets import AssetManager, AssetRequest, ResolvedAssets
from app.bible import BibleRepository
from app.config import Settings
from app.tts_engine import TTSEngine, normalize_backend, normalize_omnivoice_options
from app.utils import file_sha256, slugify, stable_hash

logger = logging.getLogger(__name__)


class GenerationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.assets = AssetManager(settings)
        self.tts = TTSEngine(
            settings.model_id,
            settings.tts_mode,
            backend=settings.tts_backend,
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
        tts_backend: str | None = None,
        model_id: str | None = None,
        omnivoice_options: dict[str, Any] | None = None,
        generation_unit: str | None = None,
    ) -> dict[str, Any]:
        if audio_format != "mp3":
            raise ValueError("Apenas format=mp3 é suportado no MVP")

        selected_backend = normalize_backend(tts_backend or self.settings.tts_backend)
        selected_model_id = model_id or self.settings.model_id
        if selected_backend != self.tts.backend:
            raise ValueError(
                "tts_backend diferente do backend carregado no worker. "
                "Reinicie a API com TTS_BACKEND=%s para usar esse backend." % selected_backend
            )
        if selected_model_id != self.tts.model_id:
            raise ValueError(
                "model_id diferente do modelo carregado no worker. "
                "Reinicie a API com MODEL_ID=%s para usar esse modelo." % selected_model_id
            )

        normalized_omnivoice_options = normalize_omnivoice_options(omnivoice_options)
        requested_generation_unit = normalize_generation_unit(generation_unit or self.settings.generation_unit)

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
        output_namespace = output_namespace_for_backend(selected_backend)
        output_root = Path(self.settings.output_dir) / output_namespace / book_slug
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
            tts_backend=selected_backend,
            model_id=selected_model_id,
            omnivoice_options=normalized_omnivoice_options,
            requested_generation_unit=requested_generation_unit,
        )

        if not force and audio_path.exists() and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("input_hash") == input_hash:
                metadata["status"] = "completed"
                metadata["audio_path"] = str(audio_path)
                metadata["audio_url"] = self._download_url(book_slug, chapter, output_namespace)
                metadata["metadata_path"] = str(metadata_path)
                metadata["metadata_url"] = self._download_url(book_slug, chapter, output_namespace, metadata=True)
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
        generation_unit_metadata: list[dict[str, Any]] = []
        intro_units, body_units = split_intro_units(content.units, include_chapter_intro)
        generation_texts, effective_generation_unit, pericope_count = generation_texts_for_content(
            content_pericopes=content.pericopes,
            body_units=body_units,
            requested_generation_unit=requested_generation_unit,
            heading_count=content.heading_count,
        )

        for intro_text in intro_units:
            wav, sample_rate = self.tts.synthesize(
                intro_text,
                language=selected_language,
                voice_clone_prompt=voice_clone_prompt,
                omnivoice_options=normalized_omnivoice_options,
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

        for unit_index, chunk_text in enumerate(generation_texts, start=1):
            wav, sample_rate = self.tts.synthesize(
                chunk_text,
                language=selected_language,
                voice_clone_prompt=voice_clone_prompt,
                omnivoice_options=normalized_omnivoice_options,
            )
            audio_chunks.append((wav, sample_rate))
            chunk_metadata.append(
                {
                    "index": len(chunk_metadata) + 1,
                    "type": effective_generation_unit,
                    "text_chars": len(chunk_text),
                    "sample_rate": sample_rate,
                }
            )
            generation_unit_metadata.append(
                {
                    "index": unit_index,
                    "type": effective_generation_unit,
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
            "model_id": selected_model_id,
            "tts_mode": self.settings.tts_mode,
            "tts_backend": selected_backend,
            "omnivoice_options": normalized_omnivoice_options,
            "requested_generation_unit": requested_generation_unit,
            "generation_unit": effective_generation_unit,
            "pericope_count": pericope_count,
            "heading_count": content.heading_count,
            "bible_db_sha256": resolved_assets.bible_db_sha256,
            "ref_audio_sha256": resolved_assets.ref_audio_sha256,
            "ref_text_sha256": resolved_assets.ref_text_sha256,
            "asset_urls": {
                "bible_db_url": resolved_assets.bible_db_url,
                "ref_audio_url": resolved_assets.ref_audio_url,
                "ref_text_url": resolved_assets.ref_text_url,
            },
            "ref_text_source": resolved_assets.ref_text_source,
            "language": selected_language,
            "include_headings": include_headings,
            "include_verse_numbers": include_verse_numbers,
            "include_chapter_intro": include_chapter_intro,
            "chapter_intro_pause_seconds": pause_seconds,
            "chunks": chunk_metadata,
            "generation_units": generation_unit_metadata,
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
            "audio_url": self._download_url(book_slug, chapter, output_namespace),
            "metadata_path": str(metadata_path),
            "metadata_url": self._download_url(book_slug, chapter, output_namespace, metadata=True),
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
        tts_backend: str,
        model_id: str,
        omnivoice_options: dict[str, Any] | None,
        requested_generation_unit: str,
    ) -> str:
        return stable_hash(
            {
                "book_id": book_id,
                "chapter": chapter,
                "full_text": full_text,
                "model_id": model_id,
                "tts_mode": self.settings.tts_mode,
                "tts_backend": tts_backend,
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
                "omnivoice_options": omnivoice_options,
                "requested_generation_unit": requested_generation_unit,
            }
        )

    def _api_url(self, path: str) -> str:
        if self.settings.public_base_url:
            return f"{self.settings.public_base_url.rstrip('/')}{path}"
        return path

    def _download_url(self, book_slug: str, chapter: int, output_namespace: str, *, metadata: bool = False) -> str:
        path = f"/download/{book_slug}/{chapter}"
        if metadata:
            path = f"{path}/metadata"
        if output_namespace != "default":
            path = f"{path}?backend={output_namespace}"
        return self._api_url(path)


def split_intro_units(units: list[str], include_chapter_intro: bool) -> tuple[list[str], list[str]]:
    if include_chapter_intro and units:
        return [units[0]], units[1:]
    return [], units


def normalize_generation_unit(value: str | None) -> str:
    unit = (value or "chapter").strip().lower()
    if unit in {"chapter", "capitulo", "capítulo"}:
        return "chapter"
    if unit in {"pericope", "perícope", "pericope_group"}:
        return "pericope"
    raise ValueError("generation_unit deve ser 'chapter' ou 'pericope'")


def generation_texts_for_content(
    *,
    content_pericopes: list[str],
    body_units: list[str],
    requested_generation_unit: str,
    heading_count: int,
) -> tuple[list[str], str, int]:
    chapter_text = " ".join(unit.strip() for unit in body_units if unit.strip())
    if not chapter_text:
        return [], requested_generation_unit, 0

    if requested_generation_unit == "pericope" and heading_count > 0 and content_pericopes:
        return content_pericopes, "pericope", len(content_pericopes)

    return [chapter_text], "chapter", 0


def output_namespace_for_backend(backend: str | None) -> str:
    if backend is None:
        return "omnivoice"
    normalized = normalize_backend(backend)
    return normalized
