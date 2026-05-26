# Workflow: Reference Work Ingestion

> The "front of house" for reference works: takes raw .txt files
> (single file or batch directory) and turns them into clean,
> chaptered, deduplicated novels in the reference DB.

## 1. Purpose

Before `reference_pipeline` can extract features, the raw upload
needs to be:

1. Decoded (UTF-8 / GB18030 / etc.)
2. Cleaned (strip BOM, normalize whitespace, drop garbled passages)
3. Split into chapters
4. Have author-notes / appended ads detected and flagged
5. Registered in the DB with metadata (title, creator, total_chars)

This module also hosts the **skill_mining** pipeline (extract reusable
writing patterns across many novels).

## 2. Who triggers it

- **`POST /api/references/works/{id}/upload`** (single file via UI)
- **`POST /api/extraction/ingest`** (batch ingest of a directory)
- **CLI**:
  - `ink extract ingest` — scan corpus + register new novels
  - `ink extract run` — run the full mining pipeline
  - `ink extract emit` — generate skills from mined patterns
- **`reference_pipeline.pipeline`** depends on this — it expects
  ingested chapters to exist in `reference_chapters` before feature
  extraction can start.

## 3. Inputs / Outputs

| Stage | In | Out |
|---|---|---|
| `NovelIngester.ingest_single(file)` | .txt path | `IngestResult{title, total_chapters, total_chars, excluded_author_notes, needs_review}` |
| `NovelIngester.ingest_all()` | corpus dir | list of IngestResult |
| `chapter_splitter.split_chapters(text)` | raw text | `[(num, title, content)]` |
| `skill_mining.orchestrator.run(...)` | corpus + ingest_dir | mining stats |

## 4. Sequence (single-file ingest)

```mermaid
sequenceDiagram
  participant CLI as CLI / API
  participant Ing as NovelIngester
  participant Det as detector (encoding + format)
  participant Split as chapter_splitter
  participant Note as author-note detector
  participant DB as reference DB

  CLI->>Ing: ingest_single("nv.txt")
  Ing->>Det: detect encoding (utf-8 / gb18030 / ...)
  Det-->>Ing: raw_text (decoded)
  Ing->>Ing: clean (strip BOM, normalize whitespace, fix line endings)
  Ing->>Split: split_chapters(text) using built-in + user patterns
  Split-->>Ing: [(num, title, content), ...]
  loop for each chapter
    Ing->>Note: detect_author_notes(content)
    Note-->>Ing: cleaned_content + excluded_section_count
  end
  Ing->>DB: insert reference_work
  Ing->>DB: insert reference_chapters (bulk)
  Ing-->>CLI: IngestResult
```

## 5. Decision points

- **Encoding detection**: tries UTF-8 first; falls back to GB18030
  (covers ~95% of Chinese web-novel .txt files). Other encodings →
  user is told to convert. We never silently mangle.
- **Chapter pattern set**: built-in patterns + user-defined patterns
  from `data/settings.json:chapter_patterns`. The detector tries each
  in order until one matches ≥3 chapters, then uses that pattern for
  the whole file.
- **Author-note detection**: scans chapter content for
  `data/settings.json:author_note_keywords` (e.g. "作者闲话",
  "感谢XXX的打赏"). Matches are excised and counted; the user can
  inspect via `/preprocess/aside_paragraphs`.
- **Dedup**: a file with the same `sha256(title + creator + first_chapter)`
  as an existing work is treated as a re-upload — content is REPLACED,
  but the `ref_id` and downstream features are preserved (so links
  from projects don't break).
- **`needs_review`**: chapters that hit the garbled-text detector or
  produce a 0-length content after cleaning are flagged for human
  review rather than dropped silently.

## 6. Error handling

- Encoding failure → HTTP 400 with the candidate encoding + first
  unparseable byte position.
- Chapter detection failure (no pattern matches ≥3 chapters) → ingest
  proceeds with one giant "chapter 1" plus a `needs_review` flag and
  a hint in the log: "no chapter pattern matched; consider adding a
  custom pattern at /chapter_patterns".
- The mining paths return structured error dicts rather than raising,
  so the UI can show the failure inline without crashing.

## 7. Related code + tests

- Source: `reference_ingest/{novel_ingester,chapter_splitter,style_extractor}.py`
- Sub-packages: `reference_ingest/skill_extraction/`
- Routers: `ui/backend/app/routers/extraction_api.py`,
  `ui/backend/app/routers/reference/works.py` (upload)
- Sister workflow: `reference_pipeline/WORKFLOW.md` (the feature
  extraction stage that runs AFTER ingestion)
