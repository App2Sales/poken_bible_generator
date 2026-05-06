from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.utils import normalize_text


@dataclass(frozen=True)
class Book:
    book_id: int
    title: str
    short_title: str | None
    chapters: int


@dataclass(frozen=True)
class ChapterContent:
    book_id: int
    book: str
    chapter: int
    text: str
    units: list[str]


class BibleRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def validate(self) -> None:
        if not Path(self.db_path).exists():
            raise FileNotFoundError(f"BIBLE_DB_PATH não existe: {self.db_path}")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("select 1 from books limit 1").fetchone()

    def get_book(self, book: str | int) -> Book:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            if isinstance(book, int) or str(book).isdigit():
                row = connection.execute(
                    "select _id, title, short_title, qtd_chapters from books where _id = ?",
                    (int(book),),
                ).fetchone()
            else:
                wanted = normalize_text(str(book))
                rows = connection.execute(
                    "select _id, title, short_title, qtd_chapters from books"
                ).fetchall()
                row = next(
                    (
                        item
                        for item in rows
                        if normalize_text(item["title"] or "") == wanted
                        or normalize_text(item["short_title"] or "") == wanted
                    ),
                    None,
                )

        if row is None:
            raise ValueError(f"Livro não encontrado: {book}")

        return Book(
            book_id=int(row["_id"]),
            title=str(row["title"]),
            short_title=row["short_title"],
            chapters=int(row["qtd_chapters"]),
        )

    def get_chapter(
        self,
        book: str | int,
        chapter: int,
        *,
        include_headings: bool,
        include_verse_numbers: bool,
        include_chapter_intro: bool,
    ) -> ChapterContent:
        resolved = self.get_book(book)
        if chapter < 1 or chapter > resolved.chapters:
            raise ValueError(f"Capítulo inválido para {resolved.title}: {chapter}")

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select verse, text, head, rank
                from texts
                where book_id = ? and chapter_num = ?
                order by rank asc
                """,
                (resolved.book_id, chapter),
            ).fetchall()

        if not rows:
            raise ValueError(f"Texto não encontrado para {resolved.title} {chapter}")

        units: list[str] = []
        if include_chapter_intro:
            units.append(f"{resolved.title}, capítulo {chapter}.")

        for row in rows:
            if row["head"] and not include_headings:
                continue
            text = " ".join(str(row["text"] or "").split())
            if not text:
                continue
            if include_verse_numbers and int(row["verse"] or 0) > 0:
                text = f"Versículo {int(row['verse'])}. {text}"
            units.append(text)

        return ChapterContent(
            book_id=resolved.book_id,
            book=resolved.title,
            chapter=chapter,
            text="\n".join(units),
            units=units,
        )
