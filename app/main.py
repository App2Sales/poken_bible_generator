from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.assets import AssetRequest
from app.config import settings
from app.service import GenerationService, output_namespace_for_backend
from app.utils import slugify

logging.basicConfig(level=logging.INFO)

service = GenerationService(settings)


class AssetsRequest(BaseModel):
    bible_db_url: str | None = None
    ref_audio_url: str | None = None
    ref_text_url: str | None = None


class OmniVoiceOptions(BaseModel):
    num_step: int | None = None
    guidance_scale: float | None = None
    denoise: bool | None = None
    speed: float | None = None
    duration: float | None = None
    preprocess_prompt: bool | None = None
    postprocess_output: bool | None = None
    instruct: str | None = None

    class Config:
        extra = "ignore"


class GenerateRequest(BaseModel):
    book: str = Field(default="Salmos")
    chapter: int
    voice_id: str | None = None
    language: str | None = None
    format: str = "mp3"
    bitrate: str = "192k"
    include_headings: bool = False
    include_verse_numbers: bool = False
    include_chapter_intro: bool = True
    chapter_intro_pause_seconds: float | None = None
    force: bool = False
    upload: bool = True
    assets: AssetsRequest | None = None
    narration_style: str | None = None
    tts_backend: str | None = None
    model_id: str | None = None
    omnivoice: OmniVoiceOptions | None = None

    class Config:
        extra = "ignore"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        service.startup()
    except Exception:
        logging.exception("Falha no startup da aplicação")
        raise
    yield


app = FastAPI(title="Spoken Bible Generator", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/voice")
def voice() -> dict[str, Any]:
    return service.voice_info()


@app.get("/download/{book}/{chapter}")
def download_audio(book: str, chapter: int, backend: str | None = None) -> FileResponse:
    book_slug = slugify(book)
    try:
        output_namespace = output_namespace_for_backend(backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audio_path = Path(settings.output_dir) / output_namespace / book_slug / f"{book_slug}_{chapter:03d}.mp3"
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Áudio não encontrado")

    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=f"{book_slug}_{chapter:03d}.mp3",
    )


@app.get("/download/{book}/{chapter}/metadata")
def download_metadata(book: str, chapter: int, backend: str | None = None) -> FileResponse:
    book_slug = slugify(book)
    try:
        output_namespace = output_namespace_for_backend(backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata_path = (
        Path(settings.output_dir)
        / output_namespace
        / book_slug
        / "metadata"
        / f"{book_slug}_{chapter:03d}.json"
    )
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Metadata não encontrado")

    return FileResponse(
        metadata_path,
        media_type="application/json",
        filename=f"{book_slug}_{chapter:03d}.json",
    )


@app.post("/generate")
def generate(request: GenerateRequest) -> dict[str, Any]:
    try:
        return service.generate_chapter(
            book=request.book,
            chapter=request.chapter,
            voice_id=request.voice_id,
            language=request.language,
            audio_format=request.format,
            bitrate=request.bitrate,
            include_headings=request.include_headings,
            include_verse_numbers=request.include_verse_numbers,
            include_chapter_intro=request.include_chapter_intro,
            chapter_intro_pause_seconds=request.chapter_intro_pause_seconds,
            force=request.force,
            upload=request.upload,
            assets=to_asset_request(request.assets),
            narration_style=request.narration_style,
            tts_backend=request.tts_backend,
            model_id=request.model_id,
            omnivoice_options=to_omnivoice_options(request.omnivoice),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def to_asset_request(assets: AssetsRequest | None) -> AssetRequest | None:
    if assets is None:
        return None
    return AssetRequest(
        bible_db_url=assets.bible_db_url,
        ref_audio_url=assets.ref_audio_url,
        ref_text_url=assets.ref_text_url,
    )


def to_omnivoice_options(options: OmniVoiceOptions | None) -> dict[str, Any] | None:
    if options is None:
        return None
    return options.model_dump(exclude_none=True)
