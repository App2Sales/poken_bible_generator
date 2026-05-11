from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.bible import VerseContent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanonicalToken:
    value: str
    verse: int
    index_in_verse: int


@dataclass(frozen=True)
class TimedToken:
    value: str
    raw: str
    start: float
    end: float


def extract_verse_timings(
    *,
    audio_path: Path,
    verses: list[VerseContent],
    output_dir: Path,
    book: str,
    chapter: int,
    language: str,
    whisper_model: str,
    whisper_device: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{slug_fragment(book)}_{chapter:03d}"
    whisper_payload = transcribe_with_whisper(
        audio_path=audio_path,
        language=language,
        model_name=whisper_model,
        device=whisper_device,
    )
    whisper_words = timed_tokens_from_whisper(whisper_payload)
    canonical_tokens = canonical_tokens_from_verses(verses)
    alignment = align_tokens(canonical_tokens, whisper_words)
    verse_timings = build_verse_timings(verses, alignment)

    result = {
        "book": book,
        "chapter": chapter,
        "language": language,
        "whisper_model": whisper_model,
        "audio_path": str(audio_path),
        "alignment": {
            "canonical_tokens": len(canonical_tokens),
            "whisper_words": len(whisper_words),
            "matched_tokens": sum(1 for item in alignment if item[1] is not None),
        },
        "verses": verse_timings,
    }

    json_path = output_dir / f"{stem}.json"
    whisper_path = output_dir / f"{stem}.whisper.json"
    srt_path = output_dir / f"{stem}.srt"
    vtt_path = output_dir / f"{stem}.vtt"

    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    whisper_path.write_text(json.dumps(whisper_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    srt_path.write_text(segments_to_srt(whisper_payload.get("segments") or []), encoding="utf-8")
    vtt_path.write_text(segments_to_vtt(whisper_payload.get("segments") or []), encoding="utf-8")

    return {
        **result,
        "timings_path": str(json_path),
        "whisper_timings_path": str(whisper_path),
        "srt_path": str(srt_path),
        "vtt_path": str(vtt_path),
    }


def transcribe_with_whisper(
    *,
    audio_path: Path,
    language: str,
    model_name: str,
    device: str | None,
) -> dict[str, Any]:
    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "openai-whisper não está instalado. Instale a dependência para extrair timings."
        ) from exc

    kwargs = {}
    if device:
        kwargs["device"] = device
    model = whisper.load_model(model_name, **kwargs)
    logger.info("Transcrevendo %s com Whisper %s", audio_path, model_name)
    result = model.transcribe(
        str(audio_path),
        language=normalize_whisper_language(language),
        task="transcribe",
        word_timestamps=True,
        verbose=False,
    )
    return result


def timed_tokens_from_whisper(payload: dict[str, Any]) -> list[TimedToken]:
    tokens: list[TimedToken] = []
    for segment in payload.get("segments") or []:
        for word in segment.get("words") or []:
            raw = str(word.get("word") or "").strip()
            value = normalize_token(raw)
            if not value:
                continue
            start = word.get("start")
            end = word.get("end")
            if start is None or end is None:
                continue
            tokens.append(TimedToken(value=value, raw=raw, start=float(start), end=float(end)))
    return tokens


def canonical_tokens_from_verses(verses: list[VerseContent]) -> list[CanonicalToken]:
    tokens: list[CanonicalToken] = []
    for verse in verses:
        index_in_verse = 0
        for token in tokenize(verse.text):
            index_in_verse += 1
            tokens.append(CanonicalToken(value=token, verse=verse.verse, index_in_verse=index_in_verse))
    return tokens


def align_tokens(
    canonical: list[CanonicalToken],
    timed: list[TimedToken],
) -> list[tuple[CanonicalToken, TimedToken | None, float]]:
    n = len(canonical)
    m = len(timed)
    if not canonical:
        return []
    if not timed:
        return [(token, None, 0.0) for token in canonical]

    gap_canonical = 1.0
    gap_timed = 0.35
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + gap_canonical
        move[i][0] = 1
    # Leading Whisper words are free so chapter intro is ignored naturally.
    for j in range(1, m + 1):
        dp[0][j] = 0.0
        move[0][j] = 2

    for i in range(1, n + 1):
        canonical_value = canonical[i - 1].value
        for j in range(1, m + 1):
            timed_value = timed[j - 1].value
            substitution = dp[i - 1][j - 1] + token_cost(canonical_value, timed_value)
            deletion = dp[i - 1][j] + gap_canonical
            insertion = dp[i][j - 1] + gap_timed
            best = substitution
            best_move = 0
            if deletion < best:
                best = deletion
                best_move = 1
            if insertion < best:
                best = insertion
                best_move = 2
            dp[i][j] = best
            move[i][j] = best_move

    # Trailing Whisper words are also free.
    j = min(range(m + 1), key=lambda column: dp[n][column])
    i = n
    aligned: list[tuple[CanonicalToken, TimedToken | None, float]] = []
    while i > 0:
        current_move = move[i][j]
        if current_move == 0 and j > 0:
            cost = token_cost(canonical[i - 1].value, timed[j - 1].value)
            if cost < 0.85:
                aligned.append((canonical[i - 1], timed[j - 1], max(0.0, 1.0 - cost)))
            else:
                aligned.append((canonical[i - 1], None, 0.0))
            i -= 1
            j -= 1
        elif current_move == 1 or j == 0:
            aligned.append((canonical[i - 1], None, 0.0))
            i -= 1
        else:
            j -= 1
    aligned.reverse()
    return aligned


def build_verse_timings(
    verses: list[VerseContent],
    alignment: list[tuple[CanonicalToken, TimedToken | None, float]],
) -> list[dict[str, Any]]:
    by_verse: dict[int, list[tuple[TimedToken, float]]] = {verse.verse: [] for verse in verses}
    total_by_verse: dict[int, int] = {verse.verse: 0 for verse in verses}
    for canonical, timed, score in alignment:
        total_by_verse[canonical.verse] = total_by_verse.get(canonical.verse, 0) + 1
        if timed is not None:
            by_verse.setdefault(canonical.verse, []).append((timed, score))

    result: list[dict[str, Any]] = []
    for verse in verses:
        matches = by_verse.get(verse.verse) or []
        total = total_by_verse.get(verse.verse, 0)
        if matches:
            starts = [item[0].start for item in matches]
            ends = [item[0].end for item in matches]
            avg_score = sum(item[1] for item in matches) / len(matches)
            coverage = len(matches) / total if total else 0.0
            confidence = round(max(0.0, min(1.0, avg_score * 0.65 + coverage * 0.35)), 3)
            result.append(
                {
                    "verse": verse.verse,
                    "start_seconds": round(min(starts), 3),
                    "end_seconds": round(max(ends), 3),
                    "confidence": confidence,
                    "matched_words": len(matches),
                    "total_words": total,
                    "method": "alignment",
                }
            )
        else:
            result.append(
                {
                    "verse": verse.verse,
                    "start_seconds": None,
                    "end_seconds": None,
                    "confidence": 0.0,
                    "matched_words": 0,
                    "total_words": total,
                    "method": "unmatched",
                }
            )

    interpolate_missing_timings(result)
    return result


def interpolate_missing_timings(items: list[dict[str, Any]]) -> None:
    for index, item in enumerate(items):
        if item["start_seconds"] is not None and item["end_seconds"] is not None:
            continue
        previous_item = next(
            (candidate for candidate in reversed(items[:index]) if candidate["end_seconds"] is not None),
            None,
        )
        next_item = next(
            (candidate for candidate in items[index + 1 :] if candidate["start_seconds"] is not None),
            None,
        )
        if previous_item and next_item:
            start = float(previous_item["end_seconds"])
            end = float(next_item["start_seconds"])
            if end < start:
                end = start
            item["start_seconds"] = round(start, 3)
            item["end_seconds"] = round(end, 3)
            item["method"] = "interpolated"
        elif previous_item:
            start = float(previous_item["end_seconds"])
            item["start_seconds"] = round(start, 3)
            item["end_seconds"] = round(start, 3)
            item["method"] = "interpolated"
        elif next_item:
            end = float(next_item["start_seconds"])
            item["start_seconds"] = round(end, 3)
            item["end_seconds"] = round(end, 3)
            item["method"] = "interpolated"


@lru_cache(maxsize=200_000)
def token_cost(left: str, right: str) -> float:
    if left == right:
        return 0.0
    ratio = SequenceMatcher(None, left, right).ratio()
    if ratio >= 0.92:
        return 0.12
    if ratio >= 0.82:
        return 0.28
    if ratio >= 0.72:
        return 0.5
    return 1.0


def tokenize(text: str) -> list[str]:
    return [token for token in (normalize_token(part) for part in re.findall(r"\w+", text, re.UNICODE)) if token]


def normalize_token(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.strip().lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def normalize_whisper_language(language: str) -> str:
    value = language.strip().lower()
    if value in {"portuguese", "portugues", "pt-br", "pt_br", "br"}:
        return "pt"
    if value in {"english", "en-us", "en_us"}:
        return "en"
    if len(value) >= 2:
        return value[:2]
    return "pt"


def segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{format_srt_time(float(segment.get('start') or 0))} --> "
            f"{format_srt_time(float(segment.get('end') or 0))}\n{str(segment.get('text') or '').strip()}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def segments_to_vtt(segments: list[dict[str, Any]]) -> str:
    body = segments_to_srt(segments).replace(",", ".")
    return "WEBVTT\n\n" + re.sub(r"^\d+\n", "", body, flags=re.MULTILINE)


def format_srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def slug_fragment(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return ascii_value or "chapter"
