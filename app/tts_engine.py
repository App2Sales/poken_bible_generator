from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TTSEngine:
    def __init__(self, model_id: str, mode: str, *, voice_id: str, x_vector_only_mode: bool):
        self.model_id = model_id
        self.mode = mode
        self.model = None
        self.voice_clone_prompt = None
        self.ref_audio_path: str | None = None
        self.ref_text_path: str | None = None
        self.ref_audio_sha256: str | None = None
        self.ref_text_sha256: str | None = None
        self.voice_id = voice_id
        self.x_vector_only_mode = x_vector_only_mode
        self._prompt_cache: dict[tuple[str, str, str | None, bool], Any] = {}

    def load_model(self) -> None:
        if self.model is not None:
            return

        if self.mode != "voice_clone":
            raise ValueError(
                f"TTS_MODE={self.mode!r} não é suportado neste MVP. Use TTS_MODE=voice_clone."
            )

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

        logger.info("Modelo TTS carregado uma vez no startup: model_id=%s mode=%s", self.model_id, self.mode)

    def get_voice_clone_prompt(
        self,
        *,
        voice_id: str,
        ref_audio_path: str,
        ref_text_path: str | None,
        ref_text: str,
        ref_audio_sha256: str,
        ref_text_sha256: str | None,
        x_vector_only_mode: bool,
    ):
        if self.model is None:
            raise RuntimeError("TTSEngine não foi carregado. Chame load_model() no startup.")

        cache_key = (voice_id, ref_audio_sha256, ref_text_sha256, x_vector_only_mode)
        if cache_key not in self._prompt_cache:
            self._prompt_cache[cache_key] = self.model.create_voice_clone_prompt(
                ref_audio=ref_audio_path,
                ref_text=ref_text,
                x_vector_only_mode=x_vector_only_mode,
            )
            logger.info(
                "Voice clone prompt criado e cacheado: voice_id=%s ref_audio_sha256=%s ref_text_sha256=%s",
                voice_id,
                ref_audio_sha256,
                ref_text_sha256,
            )

        self.voice_clone_prompt = self._prompt_cache[cache_key]
        self.voice_id = voice_id
        self.ref_audio_path = ref_audio_path
        self.ref_text_path = ref_text_path
        self.ref_audio_sha256 = ref_audio_sha256
        self.ref_text_sha256 = ref_text_sha256
        self.x_vector_only_mode = x_vector_only_mode
        return self._prompt_cache[cache_key]

    def synthesize(self, text: str, language: str = "Portuguese", *, voice_clone_prompt=None):
        if self.model is None:
            raise RuntimeError("TTSEngine não foi carregado. Chame load_model() no startup.")

        prompt = voice_clone_prompt or self.voice_clone_prompt
        if prompt is None:
            raise RuntimeError("voice_clone_prompt não foi criado para esta voz.")

        if self.mode == "voice_clone":
            wavs, sr = self.model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompt,
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
            "cached_voice_prompts": len(self._prompt_cache),
        }


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
