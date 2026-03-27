"""
Novel Corpus Ingester — scans, parses, cleans, and registers novel txt files.

Handles:
  - Filename parsing (completed vs serializing)
  - Metadata extraction (title, author, synopsis) via rules + LLM fallback
  - Chapter splitting (reuses preprocessing/chapter_splitter.py)
  - Author-note filtering (作者说, 上架感言, etc.)
  - Cleaned chapter storage to data/novel_corpus/cleaned/{title}/
  - Registration into reference_works + novel_metadata + novel_chapters tables
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from preprocessing.chapter_splitter import split_chapters

logger = logging.getLogger("inkoctobot.preprocessing.novel_ingester")


# ── Data classes ──────────────────────────────────────────────────

@dataclass
class NovelMetadata:
    title: str
    author: str | None = None
    synopsis: str | None = None
    status: str = "completed"          # 'completed' | 'serializing'
    latest_chapter: str | None = None
    metadata_end_pos: int = 0          # byte position where metadata ends
    metadata_source: str = "filename"  # 'rules' | 'llm' | 'filename'


@dataclass
class CleanedChapter:
    chapter_num: int        # re-numbered (1-based, text-only)
    original_title: str
    content: str
    char_count: int
    is_author_note: bool = False


@dataclass
class IngestResult:
    ref_id: str
    title: str
    author: str | None
    status: str
    total_chapters: int
    total_chars: int
    excluded_author_notes: int
    metadata_source: str
    needs_review: list[dict] = field(default_factory=list)


# ── Author-note detection ────────────────────────────────────────

# Title patterns that definitively indicate author notes (non-story content)
_AUTHOR_NOTE_TITLE_KEYWORDS = [
    "作者说", "作者的话", "作品相关", "上架感言", "完本感言",
    "请假", "请假条", "通知", "公告", "致读者", "番外说明",
    "关于更新", "关于本书", "新书推荐", "推书", "书友群",
    "更新说明", "调查问卷", "投票", "征集",
]

# Title patterns indicating author notes (with "感言"/"感谢" but not "番外")
_AUTHOR_NOTE_TITLE_SECONDARY = ["感言", "感谢", "致谢"]

# Content patterns suggesting author-to-reader speech
_AUTHOR_CONTENT_PATTERNS = [
    re.compile(r"(?:大家好|各位书友|读者朋友|兄弟们|书友们)"),
    re.compile(r"(?:感谢订阅|求月票|求推荐票|求打赏|求收藏|求追读)"),
    re.compile(r"(?:本书|这本书|新书)(?:已经|即将|正式)"),
    re.compile(r"(?:更新时间|更新频率|加更|请假|断更|停更)"),
    re.compile(r"(?:上架了|上架通知|vip|VIP|付费)"),
]


def is_author_note(title: str, content: str) -> tuple[bool, str]:
    """
    Determine if a chapter is an author note (non-story content).

    Returns (is_note: bool, reason: str).
    """
    title_lower = title.strip()

    # Rule 1: Definitive title keywords
    for kw in _AUTHOR_NOTE_TITLE_KEYWORDS:
        if kw in title_lower:
            return True, f"title_keyword:{kw}"

    # Rule 2: "番外" is story content, keep it
    if "番外" in title_lower:
        return False, "fanwai_kept"

    # Rule 3: Secondary title keywords
    for kw in _AUTHOR_NOTE_TITLE_SECONDARY:
        if kw in title_lower:
            return True, f"title_secondary:{kw}"

    # Rule 4: Content heuristic — if content talks to readers and is short
    if len(content) < 1500:
        matches = sum(1 for pat in _AUTHOR_CONTENT_PATTERNS if pat.search(content[:500]))
        if matches >= 2:
            return True, f"content_heuristic:matches={matches}"

    # Rule 5: Very short chapters with no narrative content
    if len(content) < 200:
        # Check if it has dialogue or narrative markers
        has_dialogue = bool(re.search(r'[「」『』""'']', content))
        has_narrative = bool(re.search(r'[他她它们我你](?:说|道|想|看|走|跑|站)', content))
        if not has_dialogue and not has_narrative:
            return True, "short_no_narrative"

    return False, ""


# ── Metadata extraction ──────────────────────────────────────────

# Patterns for explicit metadata labels
_TITLE_PAT = re.compile(
    r"^(?:书名|小说名|名称|Title)[：:\s]+(.+?)$|"
    r"^《(.+?)》\s*$",
    re.MULTILINE,
)
_AUTHOR_PAT = re.compile(
    r"^(?:作者|Author|著|作者名)[：:\s]+(.+?)$|"
    r"^(.+?)\s+著\s*$",
    re.MULTILINE,
)
_SYNOPSIS_PAT = re.compile(
    r"^(?:简介|内容简介|文案|Synopsis|摘要|介绍)[：:\s]*\n?([\s\S]+?)$",
    re.MULTILINE,
)
# First chapter heading — marks end of metadata region
_FIRST_CHAPTER_PAT = re.compile(
    r"^(?:第[一二三四五六七八九十百千零\d]+[章回节卷]|Chapter\s+\d+|[（(]\d+[)）]|【.+?】)",
    re.MULTILINE,
)


def parse_filename(filepath: Path) -> tuple[str, str, str | None]:
    """
    Parse novel filename to extract title, status, and latest chapter.

    Returns (title, status, latest_chapter).
    """
    stem = filepath.stem

    # Pattern: "书名_第XXX章 章节名" or "书名_第XXX章章节名"
    m = re.match(r"^(.+?)_(第.+)$", stem)
    if m:
        return m.group(1).strip(), "serializing", m.group(2).strip()

    return stem.strip(), "completed", None


def extract_metadata(text: str, filename: str) -> NovelMetadata:
    """
    Extract metadata (title, author, synopsis) from the beginning of a txt file.

    Strategy:
    1. Try explicit label patterns (书名：, 作者：, 简介：)
    2. Try implicit structure (first short line = title, second = author)
    3. Fall back to filename as title
    """
    # Only look at the first 5000 chars for metadata
    head = text[:5000]
    title_from_file, status, latest_chapter = parse_filename(Path(filename))

    # Find where the first chapter heading starts
    ch_match = _FIRST_CHAPTER_PAT.search(head)
    metadata_region = head[:ch_match.start()] if ch_match else head[:2000]
    metadata_end_pos = ch_match.start() if ch_match else 0

    title = None
    author = None
    synopsis = None
    source = "filename"

    # Strategy 1: Explicit labels
    m = _TITLE_PAT.search(metadata_region)
    if m:
        title = (m.group(1) or m.group(2) or "").strip()
        source = "rules"

    m = _AUTHOR_PAT.search(metadata_region)
    if m:
        author = (m.group(1) or m.group(2) or "").strip()
        if source == "filename":
            source = "rules"

    m = _SYNOPSIS_PAT.search(metadata_region)
    if m:
        raw_synopsis = m.group(1).strip()
        # Synopsis ends at the first chapter heading or after ~1000 chars
        ch_in_syn = _FIRST_CHAPTER_PAT.search(raw_synopsis)
        if ch_in_syn:
            raw_synopsis = raw_synopsis[:ch_in_syn.start()].strip()
        synopsis = raw_synopsis[:2000]  # cap length
        if source == "filename":
            source = "rules"

    # If we have title and author from explicit patterns but no synopsis,
    # check for text between last matched position and first chapter heading
    if title and not synopsis and metadata_region.strip():
        # Find text after all matched patterns, before first chapter heading
        lines = [l.strip() for l in metadata_region.split("\n") if l.strip()]
        # Skip lines that were matched as title or author
        remaining = []
        for l in lines:
            if title and title in l:
                continue
            if author and author in l:
                continue
            if _TITLE_PAT.match(l) or _AUTHOR_PAT.match(l):
                continue
            remaining.append(l)
        if remaining:
            synopsis = "\n".join(remaining)[:2000]
            if source == "filename":
                source = "rules"

    # Strategy 2: Implicit structure (if no explicit title found)
    if not title and metadata_region.strip():
        lines = [l.strip() for l in metadata_region.split("\n") if l.strip()]
        if lines:
            first_line = lines[0]
            # If first line is short and doesn't look like a chapter heading
            if len(first_line) < 50 and not _FIRST_CHAPTER_PAT.match(first_line):
                # Remove common decorators
                candidate = re.sub(r"[《》【】\[\]()（）]", "", first_line).strip()
                if candidate:
                    title = candidate
                    source = "rules"

                    # Check second line for author
                    if len(lines) > 1:
                        second = lines[1]
                        if len(second) < 30 and ("作" in second or "著" in second):
                            # Handle both "作者：XXX" and "XXX 著" patterns
                            cleaned = re.sub(r"^(?:作者|著)[：:\s]*", "", second).strip()
                            cleaned = re.sub(r"\s*著$", "", cleaned).strip()
                            author = cleaned if cleaned else None
                        elif len(second) < 20:
                            author = second

                    # Lines between title/author and first chapter = synopsis
                    start_idx = 2 if author else 1
                    if len(lines) > start_idx:
                        syn_lines = []
                        for l in lines[start_idx:]:
                            if _FIRST_CHAPTER_PAT.match(l):
                                break
                            syn_lines.append(l)
                        if syn_lines:
                            synopsis = "\n".join(syn_lines)[:2000]

    # Fallback: use filename as title
    if not title:
        title = title_from_file
        source = "filename"

    return NovelMetadata(
        title=title,
        author=author,
        synopsis=synopsis,
        status=status,
        latest_chapter=latest_chapter,
        metadata_end_pos=metadata_end_pos,
        metadata_source=source,
    )


# ── Main ingester ────────────────────────────────────────────────

def _gid(prefix: str = "ref") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _safe_dirname(title: str) -> str:
    """Create a filesystem-safe directory name from a title."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
    safe = safe.strip(". ")
    return safe[:100] or "untitled"


class NovelIngester:
    """Scan, parse, clean, and register novel txt files."""

    def __init__(self, db_path: str | Path, corpus_dir: str | Path | None = None):
        self.db_path = str(db_path)
        self.corpus_dir = Path(corpus_dir) if corpus_dir else Path("data/novel_corpus")
        self.cleaned_dir = self.corpus_dir / "cleaned"
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        from database.extraction_schema import ensure_extraction_tables
        from database.reference_schema import ensure_reference_tables
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_reference_tables(conn)
        ensure_extraction_tables(conn)
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.row_factory = sqlite3.Row
        return c

    # ── Public API ────────────────────────────────────────────

    def ingest_all(self) -> list[IngestResult]:
        """Scan corpus_dir for .txt files and ingest each one."""
        if not self.corpus_dir.exists():
            logger.warning("Corpus directory does not exist: %s", self.corpus_dir)
            return []

        txt_files = sorted(self.corpus_dir.glob("*.txt"))
        logger.info("Found %d txt files in %s", len(txt_files), self.corpus_dir)

        results = []
        for fpath in txt_files:
            try:
                result = self.ingest_single(fpath)
                if result:
                    results.append(result)
            except Exception:
                logger.exception("Failed to ingest %s", fpath.name)

        return results

    def ingest_single(self, file_path: str | Path) -> IngestResult | None:
        """
        Process a single txt file:
        1. Check if already ingested (skip if so)
        2. Read file and extract metadata
        3. Split chapters and filter author notes
        4. Write cleaned chapters to disk
        5. Register in database
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error("File not found: %s", file_path)
            return None

        # Check if already ingested
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT ref_id FROM novel_metadata WHERE source_file=?",
                (str(file_path),),
            ).fetchone()
            if existing:
                logger.info("Already ingested: %s (ref_id=%s)", file_path.name, existing["ref_id"])
                return None

        logger.info("Ingesting: %s", file_path.name)

        # Read file
        text = self._read_file(file_path)
        if not text:
            return None

        # Extract metadata
        metadata = extract_metadata(text, file_path.name)
        logger.info(
            "Metadata: title=%s, author=%s, source=%s",
            metadata.title, metadata.author, metadata.metadata_source,
        )

        # Split chapters (skip metadata region)
        body = text[metadata.metadata_end_pos:] if metadata.metadata_end_pos > 0 else text
        raw_chapters = split_chapters(body)

        # Classify chapters: story vs author-note
        cleaned_chapters: list[CleanedChapter] = []
        author_note_count = 0
        needs_review: list[dict] = []
        text_num = 0

        for ch in raw_chapters:
            title = ch.get("title", "")
            content = ch.get("content", "")
            note, reason = is_author_note(title, content)

            if note:
                author_note_count += 1
                if reason == "short_no_narrative":
                    needs_review.append({
                        "issue": "ambiguous_author_note",
                        "chapter_title": title,
                        "reason": reason,
                        "content_preview": content[:200],
                    })
                logger.debug("Filtered author note: %s (reason=%s)", title, reason)
            else:
                text_num += 1
                cleaned_chapters.append(CleanedChapter(
                    chapter_num=text_num,
                    original_title=title,
                    content=content,
                    char_count=len(content),
                ))

        if not cleaned_chapters:
            logger.warning("No story chapters found in %s", file_path.name)
            return None

        total_chars = sum(ch.char_count for ch in cleaned_chapters)
        logger.info(
            "Chapters: %d story + %d author-notes = %d total, %d chars",
            len(cleaned_chapters), author_note_count,
            len(cleaned_chapters) + author_note_count, total_chars,
        )

        # Write cleaned chapters to disk
        novel_dir = self.cleaned_dir / _safe_dirname(metadata.title)
        novel_dir.mkdir(parents=True, exist_ok=True)

        for ch in cleaned_chapters:
            ch_file = novel_dir / f"ch_{ch.chapter_num:04d}.txt"
            ch_file.write_text(
                f"{ch.original_title}\n\n{ch.content}",
                encoding="utf-8",
            )

        # Write metadata file
        meta_file = novel_dir / "metadata.json"
        meta_file.write_text(json.dumps({
            "title": metadata.title,
            "author": metadata.author,
            "synopsis": metadata.synopsis,
            "status": metadata.status,
            "latest_chapter": metadata.latest_chapter,
            "total_chapters": len(cleaned_chapters),
            "total_chars": total_chars,
            "excluded_author_notes": author_note_count,
            "source_file": str(file_path),
            "metadata_source": metadata.metadata_source,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # Register in database
        ref_id = _gid("ref")
        self._register_in_db(
            ref_id, metadata, cleaned_chapters,
            novel_dir, file_path, total_chars, author_note_count,
        )

        # Flag unusual cases for review
        if metadata.metadata_source == "filename":
            needs_review.append({
                "issue": "no_metadata",
                "reason": "Could not extract title/author from file content",
            })
        if len(cleaned_chapters) < 5:
            needs_review.append({
                "issue": "unusual_chapter_count",
                "count": len(cleaned_chapters),
            })

        return IngestResult(
            ref_id=ref_id,
            title=metadata.title,
            author=metadata.author,
            status=metadata.status,
            total_chapters=len(cleaned_chapters),
            total_chars=total_chars,
            excluded_author_notes=author_note_count,
            metadata_source=metadata.metadata_source,
            needs_review=needs_review,
        )

    # ── Internal helpers ──────────────────────────────────────

    def _read_file(self, path: Path) -> str | None:
        """Read a text file, trying common Chinese encodings."""
        for enc in ("utf-8", "gbk", "gb18030", "big5", "utf-16"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        logger.error("Cannot decode %s with any known encoding", path.name)
        return None

    def _register_in_db(
        self,
        ref_id: str,
        metadata: NovelMetadata,
        chapters: list[CleanedChapter],
        novel_dir: Path,
        source_file: Path,
        total_chars: int,
        excluded_notes: int,
    ) -> None:
        """Insert records into reference_works, novel_metadata, novel_chapters."""
        with self._conn() as conn:
            # reference_works
            conn.execute(
                """INSERT INTO reference_works
                   (ref_id, title, creator, media_type, genre, source,
                    file_path, has_full_text, preprocessing_status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (ref_id, metadata.title, metadata.author or "",
                 "web_novel", "", "novel_corpus",
                 str(source_file), 1, "pending"),
            )

            # novel_metadata
            conn.execute(
                """INSERT INTO novel_metadata
                   (ref_id, title, author, synopsis, status, latest_chapter,
                    total_chapters, total_chars, excluded_author_notes,
                    cleaned_dir, source_file)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ref_id, metadata.title, metadata.author, metadata.synopsis,
                 metadata.status, metadata.latest_chapter,
                 len(chapters), total_chars, excluded_notes,
                 str(novel_dir), str(source_file)),
            )

            # novel_chapters
            for ch in chapters:
                ch_file = novel_dir / f"ch_{ch.chapter_num:04d}.txt"
                conn.execute(
                    """INSERT INTO novel_chapters
                       (ref_id, chapter_num, original_title, file_path,
                        char_count, is_author_note)
                       VALUES (?,?,?,?,?,?)""",
                    (ref_id, ch.chapter_num, ch.original_title,
                     str(ch_file), ch.char_count, 0),
                )

            # extraction_progress — mark clean phase as completed
            conn.execute(
                """INSERT INTO extraction_progress
                   (ref_id, phase, chapter_num, status, completed_at)
                   VALUES (?,?,?,?,CURRENT_TIMESTAMP)""",
                (ref_id, "clean", 0, "completed"),
            )

            conn.commit()

    # ── Reporting ─────────────────────────────────────────────

    def get_clean_status(self) -> dict[str, Any]:
        """Generate a cleaning status report."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM novel_metadata").fetchone()[0]
            by_status = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM novel_metadata GROUP BY status",
            ).fetchall()
            total_chars = conn.execute(
                "SELECT COALESCE(SUM(total_chars), 0) FROM novel_metadata",
            ).fetchone()[0]
            total_chapters = conn.execute(
                "SELECT COALESCE(SUM(total_chapters), 0) FROM novel_metadata",
            ).fetchone()[0]
            total_notes = conn.execute(
                "SELECT COALESCE(SUM(excluded_author_notes), 0) FROM novel_metadata",
            ).fetchone()[0]

        return {
            "total_novels": total,
            "by_status": {row["status"]: row["cnt"] for row in by_status},
            "total_chapters": total_chapters,
            "total_chars": total_chars,
            "total_author_notes_removed": total_notes,
        }

    def list_novels(self) -> list[dict]:
        """List all ingested novels with their metadata."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM novel_metadata ORDER BY title",
            ).fetchall()
        return [dict(r) for r in rows]
