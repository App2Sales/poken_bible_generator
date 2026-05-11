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
class PericopeContent:
    title: str | None
    start_verse: int | None
    end_verse: int | None
    text: str


@dataclass(frozen=True)
class VerseContent:
    verse: int
    text: str


@dataclass(frozen=True)
class ChapterContent:
    book_id: int
    book: str
    chapter: int
    text: str
    units: list[str]
    verses: list[VerseContent]
    pericopes: list[PericopeContent]
    heading_count: int


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
                order by rank asc, verse asc, _id asc
                """,
                (resolved.book_id, chapter),
            ).fetchall()
            pericope_rows = []
            if table_exists(connection, "pericopes"):
                pericope_rows = connection.execute(
                    """
                    select verse, title
                    from pericopes
                    where book_id = ? and chapter_num = ?
                    order by verse asc
                    """,
                    (resolved.book_id, chapter),
                ).fetchall()

        if not rows:
            raise ValueError(f"Texto não encontrado para {resolved.title} {chapter}")

        units: list[str] = []
        if include_chapter_intro:
            units.append(f"{resolved.title}, capítulo {chapter}.")

        pericope_titles_by_verse = {
            int(row["verse"]): " ".join(str(row["title"] or "").split())
            for row in pericope_rows
            if row["verse"] is not None and str(row["title"] or "").strip()
        }
        pericopes: list[PericopeContent] = []
        verses: list[VerseContent] = []
        current_pericope: list[str] = []
        current_title: str | None = None
        current_start_verse: int | None = None
        current_end_verse: int | None = None
        heading_count = len(pericope_titles_by_verse)

        def append_current_pericope() -> None:
            if current_pericope:
                pericopes.append(
                    PericopeContent(
                        title=current_title,
                        start_verse=current_start_verse,
                        end_verse=current_end_verse,
                        text=" ".join(current_pericope),
                    )
                )

        for row in rows:
            if bool(row["head"]):
                continue

            verse = int(row["verse"] or 0)
            pericope_title = pericope_titles_by_verse.get(verse)
            if pericope_title:
                if current_pericope:
                    append_current_pericope()
                    current_pericope = []
                current_title = pericope_title
                current_start_verse = verse
                current_end_verse = None
                if include_headings:
                    units.append(pericope_title)
                    current_pericope.append(pericope_title)

            text = " ".join(str(row["text"] or "").split())
            if not text:
                continue

            if verse > 0:
                verses.append(VerseContent(verse=verse, text=text))

            if include_verse_numbers and verse > 0:
                text = f"Versículo {verse}. {text}"
            units.append(text)
            if current_start_verse is None and verse > 0:
                current_start_verse = verse
            if verse > 0:
                current_end_verse = verse
            current_pericope.append(text)

        if current_pericope:
            append_current_pericope()

        return ChapterContent(
            book_id=resolved.book_id,
            book=resolved.title,
            chapter=chapter,
            text="\n".join(units),
            units=units,
            verses=verses,
            pericopes=pericopes,
            heading_count=heading_count,
        )


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None
