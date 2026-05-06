from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any

from app.utils import file_sha256, text_sha256

logger = logging.getLogger(__name__)


class TTSEngine:
    def __init__(self, model_id: str, mode: str):
        self.model_id = model_id
        self.mode = mode
        self.model = None
        self.voice_clone_prompt = None
        self.ref_audio_path: str | None = None
        self.ref_text_path: str | None = None
        self.ref_text: str | None = None
        self.ref_audio_sha256: str | None = None
        self.ref_text_sha256: str | None = None
        self.voice_id = os.getenv("VOICE_ID", "narrador_principal")
        self.x_vector_only_mode = _env_bool("X_VECTOR_ONLY_MODE", False)

    def load_model(self) -> None:
        if self.model is not None:
            return

        if self.mode != "voice_clone":
            raise ValueError(
                f"TTS_MODE={self.mode!r} não é suportado neste MVP. Use TTS_MODE=voice_clone."
            )

        self._load_voice_reference()

        import torch

        model_cls = _import_qwen3_tts_model()
        try:
            self.model = model_cls.from_pretrained(
                self.model_id,
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation="flash_attention_2",
            )
        except Exception as exc:
            logger.warning(
                "Falha ao carregar com flash_attention_2; tentando sem attn_implementation: %s",
                exc,
            )
            self.model = model_cls.from_pretrained(
                self.model_id,
                device_map="cuda:0",
                dtype=torch.bfloat16,
            )

        self.voice_clone_prompt = self.model.create_voice_clone_prompt(
            ref_audio=self.ref_audio_path,
            ref_text=self.ref_text,
            x_vector_only_mode=self.x_vector_only_mode,
        )
        logger.info(
            "Voice clone prompt criado uma vez no startup: voice_id=%s ref_audio_sha256=%s ref_text_sha256=%s",
            self.voice_id,
            self.ref_audio_sha256,
            self.ref_text_sha256,
        )

    def synthesize(self, text: str, language: str = "Portuguese"):
        if self.model is None or self.voice_clone_prompt is None:
            raise RuntimeError("TTSEngine não foi carregado. Chame load_model() no startup.")

        if self.mode == "voice_clone":
            wavs, sr = self.model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=self.voice_clone_prompt,
            )
            return wavs[0], sr

        raise ValueError(f"TTS_MODE não suportado: {self.mode}")

    def voice_info(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "ref_audio_path_exists": bool(self.ref_audio_path and Path(self.ref_audio_path).exists()),
            "ref_text_path_exists": bool(self.ref_text_path and Path(self.ref_text_path).exists()),
            "ref_audio_sha256": self.ref_audio_sha256,
            "ref_text_sha256": self.ref_text_sha256,
            "x_vector_only_mode": self.x_vector_only_mode,
        }

    def _load_voice_reference(self) -> None:
        self.ref_audio_path = os.getenv("REF_AUDIO_PATH", "/data/voices/narrador.wav")
        self.ref_text_path = os.getenv("REF_TEXT_PATH", "/data/voices/narrador.txt")
        self.x_vector_only_mode = _env_bool("X_VECTOR_ONLY_MODE", False)

        audio_path = Path(self.ref_audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"REF_AUDIO_PATH não existe: {self.ref_audio_path}")

        text_path = Path(self.ref_text_path)
        if not text_path.exists():
            if self.x_vector_only_mode:
                logger.warning(
                    "REF_TEXT_PATH não existe e X_VECTOR_ONLY_MODE=true; modo permitido apenas para teste e com qualidade potencialmente menor."
                )
                self.ref_text = ""
                self.ref_text_sha256 = None
            else:
                raise FileNotFoundError(
                    "REF_TEXT_PATH não existe: "
                    f"{self.ref_text_path}. REF_TEXT é obrigatório quando TTS_MODE=voice_clone e X_VECTOR_ONLY_MODE=false."
                )
        else:
            self.ref_text = text_path.read_text(encoding="utf-8").strip()
            if not self.ref_text and not self.x_vector_only_mode:
                raise ValueError(
                    "REF_TEXT_PATH está vazio. REF_TEXT é obrigatório quando TTS_MODE=voice_clone e X_VECTOR_ONLY_MODE=false."
                )
            self.ref_text_sha256 = text_sha256(self.ref_text)

        if self.x_vector_only_mode:
            logger.warning(
                "X_VECTOR_ONLY_MODE=true está ativo. Use apenas para teste; em produção use X_VECTOR_ONLY_MODE=false."
            )

        self.ref_audio_sha256 = file_sha256(audio_path)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _import_qwen3_tts_model():
    candidates = (
        ("qwen3_tts", "Qwen3TTSModel"),
        ("qwen_tts", "Qwen3TTSModel"),
        ("qwen3_tts.modeling_qwen3_tts", "Qwen3TTSModel"),
    )
    errors: list[str] = []
    for module_name, attr_name in candidates:
        try:
            module = importlib.import_module(module_name)
            return getattr(module, attr_name)
        except Exception as exc:
            errors.append(f"{module_name}.{attr_name}: {exc}")

    raise ImportError(
        "Não foi possível importar Qwen3TTSModel. Instale o pacote do Qwen3 TTS que expõe "
        "Qwen3TTSModel.from_pretrained(...). Tentativas: " + "; ".join(errors)
    )
