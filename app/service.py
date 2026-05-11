from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.audio import probe_duration_seconds, silence, write_audio_file
from app.assets import AssetManager, AssetRequest, ResolvedAssets
from app.bible import BibleRepository, PericopeContent
from app.config import Settings
from app.timings import extract_verse_timings as extract_verse_timings_file
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
        pericope_pause_seconds: float | None,
        force: bool,
        upload: bool,
        assets: AssetRequest | None = None,
        narration_style: str | None = None,
        tts_backend: str | None = None,
        model_id: str | None = None,
        omnivoice_options: dict[str, Any] | None = None,
        generation_unit: str | None = None,
        extract_verse_timings: bool | None = None,
        whisper_model: str | None = None,
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
        should_extract_verse_timings = (
            extract_verse_timings
            if extract_verse_timings is not None
            else self.settings.extract_verse_timings
        )
        selected_whisper_model = whisper_model or self.settings.whisper_model

        pause_seconds = (
            chapter_intro_pause_seconds
            if chapter_intro_pause_seconds is not None
            else self.settings.chapter_intro_pause_seconds
        )
        if pause_seconds < 0 or pause_seconds > 10:
            raise ValueError("chapter_intro_pause_seconds deve estar entre 0 e 10")
        if not include_chapter_intro:
            pause_seconds = 0

        selected_pericope_pause_seconds = (
            pericope_pause_seconds
            if pericope_pause_seconds is not None
            else self.settings.pericope_pause_seconds
        )
        if selected_pericope_pause_seconds < 0 or selected_pericope_pause_seconds > 10:
            raise ValueError("pericope_pause_seconds deve estar entre 0 e 10")

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
            pericope_pause_seconds=selected_pericope_pause_seconds,
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
                if should_extract_verse_timings and not self._timing_paths(output_root, chapter_name)["json"].exists():
                    metadata["verse_timings"] = self._extract_verse_timings(
                        audio_path=audio_path,
                        output_root=output_root,
                        chapter_name=chapter_name,
                        content=content,
                        language=selected_language,
                        whisper_model=selected_whisper_model,
                        output_namespace=output_namespace,
                    )
                    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
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
        generation_units, effective_generation_unit, pericope_count = generation_units_for_content(
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

        for unit_index, generation_unit in enumerate(generation_units, start=1):
            chunk_text = str(generation_unit["text"])
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
            unit_metadata = {
                "index": unit_index,
                "type": generation_unit["type"],
                "text_chars": len(chunk_text),
                "sample_rate": sample_rate,
            }
            if generation_unit.get("title"):
                unit_metadata["title"] = generation_unit["title"]
            if generation_unit.get("start_verse") is not None:
                unit_metadata["start_verse"] = generation_unit["start_verse"]
            if generation_unit.get("end_verse") is not None:
                unit_metadata["end_verse"] = generation_unit["end_verse"]
            generation_unit_metadata.append(unit_metadata)
            if (
                effective_generation_unit == "pericope"
                and selected_pericope_pause_seconds > 0
                and unit_index < len(generation_units)
            ):
                audio_chunks.append((silence(selected_pericope_pause_seconds, sample_rate), sample_rate))
                chunk_metadata.append(
                    {
                        "index": len(chunk_metadata) + 1,
                        "type": "pericope_pause",
                        "duration_seconds": selected_pericope_pause_seconds,
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
            "pericope_pause_seconds": selected_pericope_pause_seconds,
            "extract_verse_timings": should_extract_verse_timings,
            "whisper_model": selected_whisper_model,
            "chunks": chunk_metadata,
            "generation_units": generation_unit_metadata,
            "duration_seconds": duration_seconds,
            "sha256": audio_sha256,
            "input_hash": input_hash,
        }
        if should_extract_verse_timings:
            metadata["verse_timings"] = self._extract_verse_timings(
                audio_path=audio_path,
                output_root=output_root,
                chapter_name=chapter_name,
                content=content,
                language=selected_language,
                whisper_model=selected_whisper_model,
                output_namespace=output_namespace,
            )
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

    def generate_verse_timings(
        self,
        *,
        book: str,
        chapter: int,
        language: str | None = None,
        tts_backend: str | None = None,
        whisper_model: str | None = None,
        assets: AssetRequest | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        selected_backend = normalize_backend(tts_backend or self.settings.tts_backend)
        selected_language = language or self.settings.default_language
        selected_whisper_model = whisper_model or self.settings.whisper_model
        resolved_assets = self.assets.resolve(
            assets,
            x_vector_only_mode=self.settings.x_vector_only_mode,
            force_download=False,
        )
        bible = BibleRepository(resolved_assets.bible_db_path)
        bible.validate()
        content = bible.get_chapter(
            book,
            chapter,
            include_headings=False,
            include_verse_numbers=False,
            include_chapter_intro=False,
        )
        book_slug = slugify(content.book)
        chapter_name = f"{book_slug}_{chapter:03d}"
        output_namespace = output_namespace_for_backend(selected_backend)
        output_root = Path(self.settings.output_dir) / output_namespace / book_slug
        audio_path = output_root / f"{chapter_name}.mp3"
        if not audio_path.exists():
            raise FileNotFoundError(f"Áudio não encontrado: {audio_path}")

        paths = self._timing_paths(output_root, chapter_name)
        if not force and paths["json"].exists():
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            return {
                "status": "completed",
                **payload,
                **self._timing_urls(book_slug, chapter, output_namespace),
            }

        result = self._extract_verse_timings(
            audio_path=audio_path,
            output_root=output_root,
            chapter_name=chapter_name,
            content=content,
            language=selected_language,
            whisper_model=selected_whisper_model,
            output_namespace=output_namespace,
            fail_on_error=True,
        )
        return {"status": "completed", **result}

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
        pericope_pause_seconds: float,
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
                "pericope_pause_seconds": pericope_pause_seconds,
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

    def _timing_paths(self, output_root: Path, chapter_name: str) -> dict[str, Path]:
        root = output_root / "timings"
        return {
            "json": root / f"{chapter_name}.json",
            "whisper-json": root / f"{chapter_name}.whisper.json",
            "srt": root / f"{chapter_name}.srt",
            "vtt": root / f"{chapter_name}.vtt",
        }

    def _timing_urls(self, book_slug: str, chapter: int, output_namespace: str) -> dict[str, str]:
        return {
            "timings_url": self._timings_url(book_slug, chapter, output_namespace, "json"),
            "whisper_timings_url": self._timings_url(book_slug, chapter, output_namespace, "whisper-json"),
            "srt_url": self._timings_url(book_slug, chapter, output_namespace, "srt"),
            "vtt_url": self._timings_url(book_slug, chapter, output_namespace, "vtt"),
        }

    def _timings_url(self, book_slug: str, chapter: int, output_namespace: str, fmt: str) -> str:
        path = f"/download/{book_slug}/{chapter}/timings?format={fmt}"
        if output_namespace != "default":
            path = f"{path}&backend={output_namespace}"
        return self._api_url(path)

    def _extract_verse_timings(
        self,
        *,
        audio_path: Path,
        output_root: Path,
        chapter_name: str,
        content: Any,
        language: str,
        whisper_model: str,
        output_namespace: str,
        fail_on_error: bool = False,
    ) -> dict[str, Any]:
        try:
            result = extract_verse_timings_file(
                audio_path=audio_path,
                verses=content.verses,
                output_dir=output_root / "timings",
                book=content.book,
                chapter=content.chapter,
                language=language,
                whisper_model=whisper_model,
                whisper_device=self.settings.whisper_device,
            )
            book_slug = slugify(content.book)
            return {
                "status": "completed",
                "chapter_name": chapter_name,
                "path": result["timings_path"],
                "whisper_path": result["whisper_timings_path"],
                "srt_path": result["srt_path"],
                "vtt_path": result["vtt_path"],
                **self._timing_urls(book_slug, content.chapter, output_namespace),
                "alignment": result.get("alignment"),
            }
        except Exception as exc:
            if fail_on_error:
                raise
            logger.exception("Falha ao extrair timings de versículo")
            return {"status": "failed", "error": repr(exc), "whisper_model": whisper_model}


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


def generation_units_for_content(
    *,
    content_pericopes: list[PericopeContent],
    body_units: list[str],
    requested_generation_unit: str,
    heading_count: int,
) -> tuple[list[dict[str, Any]], str, int]:
    chapter_text = " ".join(unit.strip() for unit in body_units if unit.strip())
    if not chapter_text:
        return [], requested_generation_unit, 0

    if requested_generation_unit == "pericope" and heading_count > 0 and content_pericopes:
        units = [
            {
                "type": "pericope",
                "text": pericope.text,
                "title": pericope.title,
                "start_verse": pericope.start_verse,
                "end_verse": pericope.end_verse,
            }
            for pericope in content_pericopes
            if pericope.text.strip()
        ]
        return units, "pericope", len(units)

    return [{"type": "chapter", "text": chapter_text}], "chapter", 0


def output_namespace_for_backend(backend: str | None) -> str:
    if backend is None:
        return "omnivoice"
    normalized = normalize_backend(backend)
    return normalized
