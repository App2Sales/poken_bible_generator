from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3600) as response:
        return json.loads(response.read().decode("utf-8"))


def load_chapters(db_path: str, only_book: str | None, skip_books: set[str]) -> list[tuple[str, int]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("select title, qtd_chapters from books order by _id asc").fetchall()

    chapters: list[tuple[str, int]] = []
    for title, qtd_chapters in rows:
        if title in skip_books:
            continue
        if only_book and title != only_book:
            continue
        for chapter in range(1, int(qtd_chapters) + 1):
            chapters.append((title, chapter))
    return chapters


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera timings por versículo para áudios já existentes.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--bible-db", default="bibles/naa.db")
    parser.add_argument("--book")
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--skip-book", action="append", default=[])
    parser.add_argument("--language", default="Portuguese")
    parser.add_argument("--tts-backend")
    parser.add_argument("--whisper-model")
    parser.add_argument("--bible-db-url")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.chapter and not args.book:
        parser.error("--chapter exige --book")

    chapters = [(args.book, args.chapter)] if args.book and args.chapter else load_chapters(
        args.bible_db,
        only_book=args.book,
        skip_books=set(args.skip_book),
    )
    api_base = args.api_base_url.rstrip("/")

    for book, chapter in chapters:
        payload = {
            "language": args.language,
            "force": args.force,
        }
        if args.tts_backend:
            payload["tts_backend"] = args.tts_backend
        if args.whisper_model:
            payload["whisper_model"] = args.whisper_model
        if args.bible_db_url:
            payload["assets"] = {"bible_db_url": args.bible_db_url}

        try:
            result = post_json(f"{api_base}/timings/{urllib.parse.quote(book)}/{chapter}", payload)
        except urllib.error.HTTPError as exc:
            sys.stderr.write(f"{book} {chapter}: erro HTTP {exc.code}: {exc.read().decode('utf-8')}\n")
            return 1
        except Exception as exc:
            sys.stderr.write(f"{book} {chapter}: erro: {exc}\n")
            return 1

        print(f"{book} {chapter}: {result.get('status')} {result.get('timings_url') or result.get('path')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
