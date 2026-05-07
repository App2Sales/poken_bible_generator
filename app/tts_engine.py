from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_OMNIVOICE_OPTIONS = {
    "num_step": 32,
    "guidance_scale": 2.0,
    "denoise": True,
    "speed": 1.0,
    "duration": None,
    "preprocess_prompt": True,
    "postprocess_output": True,
    "instruct": "",
}


class TTSEngine:
    def __init__(
        self,
        model_id: str,
        mode: str,
        *,
        backend: str,
        voice_id: str,
        x_vector_only_mode: bool,
    ):
        self.model_id = model_id
        self.mode = mode
        self.backend = normalize_backend(backend)
        self.model = None
        self.voice_clone_prompt = None
        self.sampling_rate: int | None = None
        self.ref_audio_path: str | None = None
        self.ref_text_path: str | None = None
        self.ref_audio_sha256: str | None = None
        self.ref_text_sha256: str | None = None
        self.voice_id = voice_id
        self.x_vector_only_mode = x_vector_only_mode
        self._prompt_cache: dict[tuple[str, str, str | None, bool, str], Any] = {}

    def load_model(self) -> None:
        if self.model is not None:
            return

        if self.mode != "voice_clone":
            raise ValueError(
                f"TTS_MODE={self.mode!r} não é suportado neste MVP. Use TTS_MODE=voice_clone."
            )

        if self.backend == "qwen3":
            self._load_qwen3_model()
        elif self.backend == "omnivoice":
            self._load_omnivoice_model()
        else:
            raise ValueError(f"TTS_BACKEND não suportado: {self.backend}")

        logger.info(
            "Modelo TTS carregado uma vez no startup: backend=%s model_id=%s mode=%s",
            self.backend,
            self.model_id,
            self.mode,
        )

    def _load_qwen3_model(self) -> None:
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

    def _load_omnivoice_model(self) -> None:
        import torch

        module = importlib.import_module("omnivoice")
        model_cls = getattr(module, "OmniVoice")
        self.model = model_cls.from_pretrained(
            self.model_id,
            device_map="cuda",
            dtype=torch.float16,
            load_asr=True,
        )
        self.sampling_rate = int(self.model.sampling_rate)

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

        cache_key = (voice_id, ref_audio_sha256, ref_text_sha256, x_vector_only_mode, self.backend)
        if cache_key not in self._prompt_cache:
            if self.backend == "qwen3":
                self._prompt_cache[cache_key] = self.model.create_voice_clone_prompt(
                    ref_audio=ref_audio_path,
                    ref_text=ref_text,
                    x_vector_only_mode=x_vector_only_mode,
                )
            elif self.backend == "omnivoice":
                if x_vector_only_mode:
                    logger.warning("OmniVoice não usa X_VECTOR_ONLY_MODE; usando ref_text quando disponível.")
                self._prompt_cache[cache_key] = self.model.create_voice_clone_prompt(
                    ref_audio=ref_audio_path,
                    ref_text=ref_text,
                )
            else:
                raise ValueError(f"TTS_BACKEND não suportado: {self.backend}")
            logger.info(
                "Voice clone prompt criado e cacheado: backend=%s voice_id=%s ref_audio_sha256=%s ref_text_sha256=%s",
                self.backend,
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

    def synthesize(
        self,
        text: str,
        language: str = "Portuguese",
        *,
        voice_clone_prompt=None,
        omnivoice_options: dict[str, Any] | None = None,
    ):
        if self.model is None:
            raise RuntimeError("TTSEngine não foi carregado. Chame load_model() no startup.")

        prompt = voice_clone_prompt or self.voice_clone_prompt
        if prompt is None:
            raise RuntimeError("voice_clone_prompt não foi criado para esta voz.")

        if self.mode == "voice_clone" and self.backend == "qwen3":
            wavs, sr = self.model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompt,
            )
            return wavs[0], sr

        if self.mode == "voice_clone" and self.backend == "omnivoice":
            options = normalize_omnivoice_options(omnivoice_options)
            module = importlib.import_module("omnivoice")
            config_cls = getattr(module, "OmniVoiceGenerationConfig")
            generation_config = config_cls(
                num_step=options["num_step"],
                guidance_scale=options["guidance_scale"],
                denoise=options["denoise"],
                preprocess_prompt=options["preprocess_prompt"],
                postprocess_output=options["postprocess_output"],
            )
            kwargs: dict[str, Any] = {
                "text": text.strip(),
                "language": language if language and language != "Auto" else None,
                "voice_clone_prompt": prompt,
                "generation_config": generation_config,
            }
            if options["speed"] != 1.0:
                kwargs["speed"] = options["speed"]
            if options["duration"] is not None and options["duration"] > 0:
                kwargs["duration"] = options["duration"]
            if options["instruct"]:
                kwargs["instruct"] = options["instruct"]

            audio = self.model.generate(**kwargs)
            return audio[0], self.sampling_rate

        raise ValueError(f"TTS_MODE não suportado: {self.mode}")

    def voice_info(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "tts_backend": self.backend,
            "model_id": self.model_id,
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


def normalize_backend(value: str | None) -> str:
    backend = (value or "qwen3").strip().lower()
    if backend in {"qwen", "qwen3", "qwen3_tts"}:
        return "qwen3"
    if backend in {"omnivoice", "omni_voice"}:
        return "omnivoice"
    raise ValueError(f"TTS_BACKEND não suportado: {value}")


def normalize_omnivoice_options(options: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_OMNIVOICE_OPTIONS, **(options or {})}
    normalized = {
        "num_step": int(merged["num_step"]),
        "guidance_scale": float(merged["guidance_scale"]),
        "denoise": _coerce_bool(merged["denoise"]),
        "speed": float(merged["speed"]),
        "duration": None if merged["duration"] is None else float(merged["duration"]),
        "preprocess_prompt": _coerce_bool(merged["preprocess_prompt"]),
        "postprocess_output": _coerce_bool(merged["postprocess_output"]),
        "instruct": str(merged["instruct"] or "").strip(),
    }
    if normalized["num_step"] <= 0:
        raise ValueError("omnivoice.num_step deve ser maior que 0")
    if normalized["guidance_scale"] <= 0:
        raise ValueError("omnivoice.guidance_scale deve ser maior que 0")
    if normalized["speed"] <= 0:
        raise ValueError("omnivoice.speed deve ser maior que 0")
    if normalized["duration"] is not None and normalized["duration"] < 0:
        raise ValueError("omnivoice.duration não pode ser negativo")
    return normalized


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
