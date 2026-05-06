from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.assets import AssetRequest
from app.config import settings
from app.service import GenerationService

logging.basicConfig(level=logging.INFO)

service = GenerationService(settings)


class AssetsRequest(BaseModel):
    bible_db_url: str | None = None
    ref_audio_url: str | None = None
    ref_text_url: str | None = None


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
    force: bool = False
    upload: bool = True
    assets: AssetsRequest | None = None
    narration_style: str | None = None

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
            force=request.force,
            upload=request.upload,
            assets=to_asset_request(request.assets),
            narration_style=request.narration_style,
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
