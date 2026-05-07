from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def default_bible_db_path() -> str:
    configured = os.getenv("BIBLE_DB_PATH")
    if configured:
        return configured

    local_db = Path("bibles/naa.db")
    if local_db.exists():
        return str(local_db)

    return "/data/bible.sqlite"


@dataclass(frozen=True)
class Settings:
    bible_db_path: str = default_bible_db_path()
    output_dir: str = os.getenv("OUTPUT_DIR", "/outputs")
    asset_cache_dir: str = os.getenv("ASSET_CACHE_DIR", "/data/assets")
    model_id: str = os.getenv("MODEL_ID", DEFAULT_MODEL_ID)
    tts_mode: str = os.getenv("TTS_MODE", "voice_clone")
    ref_audio_path: str = os.getenv("REF_AUDIO_PATH", "/data/voices/narrador.wav")
    ref_text_path: str = os.getenv("REF_TEXT_PATH", "/data/voices/narrador.txt")
    voice_id: str = os.getenv("VOICE_ID", "narrador_principal")
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "Portuguese")
    x_vector_only_mode: bool = env_bool("X_VECTOR_ONLY_MODE", False)
    chunk_max_chars: int = int(os.getenv("CHUNK_MAX_CHARS", "400"))
    chapter_intro_pause_seconds: float = float(os.getenv("CHAPTER_INTRO_PAUSE_SECONDS", "1.0"))
    public_base_url: str | None = os.getenv("PUBLIC_BASE_URL")


settings = Settings()
