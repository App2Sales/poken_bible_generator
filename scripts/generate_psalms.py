from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera Salmos 1 a 150 pela API local/remota.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/generate")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=150)
    parser.add_argument("--voice-id", default="narrador_principal")
    parser.add_argument("--language", default="Portuguese")
    parser.add_argument("--tts-backend")
    parser.add_argument("--model-id")
    parser.add_argument("--omnivoice-num-step", type=int)
    parser.add_argument("--omnivoice-guidance-scale", type=float)
    parser.add_argument("--omnivoice-denoise", action="store_true")
    parser.add_argument("--omnivoice-speed", type=float)
    parser.add_argument("--omnivoice-duration", type=float)
    parser.add_argument("--omnivoice-instruct")
    parser.add_argument("--bible-db-url")
    parser.add_argument("--ref-audio-url")
    parser.add_argument("--ref-text-url")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    assets = {
        key: value
        for key, value in {
            "bible_db_url": args.bible_db_url,
            "ref_audio_url": args.ref_audio_url,
            "ref_text_url": args.ref_text_url,
        }.items()
        if value
    }

    for chapter in range(args.start, args.end + 1):
        payload = {
            "book": "Salmos",
            "chapter": chapter,
            "voice_id": args.voice_id,
            "language": args.language,
            "format": "mp3",
            "bitrate": "192k",
            "include_headings": False,
            "include_verse_numbers": False,
            "include_chapter_intro": True,
            "force": args.force,
            "upload": not args.no_upload,
        }
        if args.tts_backend:
            payload["tts_backend"] = args.tts_backend
        if args.model_id:
            payload["model_id"] = args.model_id
        omnivoice = {
            key: value
            for key, value in {
                "num_step": args.omnivoice_num_step,
                "guidance_scale": args.omnivoice_guidance_scale,
                "denoise": True if args.omnivoice_denoise else None,
                "speed": args.omnivoice_speed,
                "duration": args.omnivoice_duration,
                "instruct": args.omnivoice_instruct,
            }.items()
            if value is not None
        }
        if omnivoice:
            payload["omnivoice"] = omnivoice
        if assets:
            payload["assets"] = assets
        try:
            result = post_json(args.api_url, payload)
        except urllib.error.HTTPError as exc:
            sys.stderr.write(f"Salmo {chapter}: erro HTTP {exc.code}: {exc.read().decode('utf-8')}\n")
            return 1
        except Exception as exc:
            sys.stderr.write(f"Salmo {chapter}: erro: {exc}\n")
            return 1

        print(f"Salmo {chapter}: {result.get('status')} {result.get('audio_url') or result.get('audio_path')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
