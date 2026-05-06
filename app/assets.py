from __future__ import annotations

import logging
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.utils import file_sha256, text_sha256

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetRequest:
    bible_db_url: str | None = None
    ref_audio_url: str | None = None
    ref_text_url: str | None = None


@dataclass(frozen=True)
class ResolvedAssets:
    bible_db_path: str
    ref_audio_path: str
    ref_text_path: str | None
    ref_text: str
    bible_db_sha256: str
    ref_audio_sha256: str
    ref_text_sha256: str | None
    bible_db_url: str | None
    ref_audio_url: str | None
    ref_text_url: str | None


class AssetManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = Path(settings.asset_cache_dir)
        self.max_download_bytes = int(os.getenv("ASSET_MAX_DOWNLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))

    def resolve(self, request: AssetRequest | None, *, x_vector_only_mode: bool, force_download: bool = False) -> ResolvedAssets:
        request = request or AssetRequest()
        bible_db_path = self._path_from_url_or_default(
            request.bible_db_url,
            default_path=self.settings.bible_db_path,
            subdir="db",
            suffix=".sqlite",
            force_download=force_download,
        )
        ref_audio_path = self._path_from_url_or_default(
            request.ref_audio_url,
            default_path=self.settings.ref_audio_path,
            subdir="voices",
            suffix=".wav",
            force_download=force_download,
        )

        if request.ref_text_url:
            ref_text_path = self._download(
                request.ref_text_url,
                subdir="voices",
                suffix=".txt",
                force_download=force_download,
            )
        else:
            ref_text_path = Path(self.settings.ref_text_path)

        self._require_file(bible_db_path, "BIBLE_DB_PATH")
        self._require_file(ref_audio_path, "REF_AUDIO_PATH")

        if not ref_text_path.exists():
            if x_vector_only_mode:
                logger.warning(
                    "REF_TEXT_PATH não existe e X_VECTOR_ONLY_MODE=true; modo permitido apenas para teste e com qualidade potencialmente menor."
                )
                ref_text = ""
                ref_text_sha256 = None
            else:
                raise FileNotFoundError(
                    "REF_TEXT_PATH não existe: "
                    f"{ref_text_path}. REF_TEXT é obrigatório quando TTS_MODE=voice_clone e X_VECTOR_ONLY_MODE=false."
                )
        else:
            ref_text = ref_text_path.read_text(encoding="utf-8").strip()
            if not ref_text and not x_vector_only_mode:
                raise ValueError(
                    "REF_TEXT_PATH está vazio. REF_TEXT é obrigatório quando TTS_MODE=voice_clone e X_VECTOR_ONLY_MODE=false."
                )
            ref_text_sha256 = file_sha256(ref_text_path)

        return ResolvedAssets(
            bible_db_path=str(bible_db_path),
            ref_audio_path=str(ref_audio_path),
            ref_text_path=str(ref_text_path) if ref_text_path.exists() else None,
            ref_text=ref_text,
            bible_db_sha256=file_sha256(bible_db_path),
            ref_audio_sha256=file_sha256(ref_audio_path),
            ref_text_sha256=ref_text_sha256,
            bible_db_url=request.bible_db_url,
            ref_audio_url=request.ref_audio_url,
            ref_text_url=request.ref_text_url,
        )

    def _path_from_url_or_default(
        self,
        url: str | None,
        *,
        default_path: str,
        subdir: str,
        suffix: str,
        force_download: bool,
    ) -> Path:
        if url:
            return self._download(url, subdir=subdir, suffix=suffix, force_download=force_download)
        return Path(default_path)

    def _download(self, url: str, *, subdir: str, suffix: str, force_download: bool) -> Path:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"URL de asset inválida; use http/https: {url}")

        destination_dir = self.cache_dir / subdir
        destination_dir.mkdir(parents=True, exist_ok=True)
        url_cache_path = destination_dir / f"url-{text_sha256(url)}.txt"
        if url_cache_path.exists() and not force_download:
            cached_path = Path(url_cache_path.read_text(encoding="utf-8").strip())
            if cached_path.exists():
                return cached_path

        with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            try:
                logger.info("Baixando asset: %s", url)
                with urllib.request.urlopen(url, timeout=300) as response:
                    total = 0
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > self.max_download_bytes:
                            raise ValueError(f"Asset excede ASSET_MAX_DOWNLOAD_BYTES: {url}")
                        temp_file.write(block)
            except urllib.error.URLError as exc:
                temp_path.unlink(missing_ok=True)
                raise ValueError(f"Falha ao baixar asset {url}: {exc}") from exc
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        digest = file_sha256(temp_path)
        final_path = destination_dir / f"{digest}{suffix}"
        if final_path.exists():
            temp_path.unlink(missing_ok=True)
            url_cache_path.write_text(str(final_path), encoding="utf-8")
            return final_path

        temp_path.replace(final_path)
        url_cache_path.write_text(str(final_path), encoding="utf-8")
        return final_path

    @staticmethod
    def _require_file(path: Path, env_name: str) -> None:
        if not path.exists():
            raise FileNotFoundError(f"{env_name} não existe: {path}")
