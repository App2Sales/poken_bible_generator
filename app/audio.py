from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def write_audio_file(chunks: list[tuple[Any, int]], output_path: Path, bitrate: str) -> float:
    if not chunks:
        raise ValueError("Nenhum chunk de áudio foi gerado")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = 0.0

    with tempfile.TemporaryDirectory(prefix="spoken-bible-") as temp_dir:
        temp_path = Path(temp_dir)
        list_path = temp_path / "chunks.txt"
        wav_paths: list[Path] = []

        for index, (wav, sample_rate) in enumerate(chunks):
            audio = _to_numpy_audio(wav)
            duration_seconds += len(audio) / float(sample_rate)
            wav_path = temp_path / f"chunk_{index:04d}.wav"
            sf.write(wav_path, audio, sample_rate)
            wav_paths.append(wav_path)

        list_path.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in wav_paths),
            encoding="utf-8",
        )

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(output_path),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)

    return duration_seconds


def probe_duration_seconds(path: Path, fallback: float) -> float:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        return float(payload["format"]["duration"])
    except Exception:
        return fallback


def _to_numpy_audio(wav: Any) -> np.ndarray:
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().float().numpy()
    elif hasattr(wav, "cpu"):
        wav = wav.cpu().numpy()
    else:
        wav = np.asarray(wav)

    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim == 2 and wav.shape[0] <= 2 and wav.shape[1] > wav.shape[0]:
        wav = wav.T
    return wav.squeeze()
