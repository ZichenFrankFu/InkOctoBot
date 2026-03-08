"""
Character Profiler — automatic character extraction from reference texts.

README §5.1: Extracts character names, traits, relationships from
reference work chapters using NLP heuristics + optional LLM enhancement.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.character_profiler")


class CharacterProfiler:
    """Extract character profiles from text."""

    # Common Chinese name patterns (2-3 character surnames + given names)
    _NAME_PATTERN = re.compile(
        r"(?:[\u4e00-\u9fa5]{2,4})(?=说|道|笑|叹|喊|叫|问|答|点头|摇头|看向|转身|走向)"
    )
    # Dialogue extraction
    _DIALOGUE_PATTERN = re.compile(r"[""「](.+?)[""」]")

    def extract_characters(
        self,
        chapters: list[dict[str, Any]],
        *,
        min_mentions: int = 3,
        top_n: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Extract character profiles from chapter list.

        Returns list of {name, mention_count, first_appearance, dialogues, co_occurrences}.
        """
        all_text = "\n".join(ch.get("content", "") for ch in chapters)
        name_counter: Counter[str] = Counter()
        first_seen: dict[str, int] = {}
        dialogues: dict[str, list[str]] = {}
        co_occurrences: dict[str, Counter[str]] = {}

        for ch in chapters:
            ch_num = ch.get("chapter_num", 0)
            content = ch.get("content", "")

            # Extract names
            names_in_chapter = set()
            for m in self._NAME_PATTERN.finditer(content):
                name = m.group()
                if len(name) < 2 or len(name) > 4:
                    continue
                name_counter[name] += 1
                names_in_chapter.add(name)
                if name not in first_seen:
                    first_seen[name] = ch_num

            # Extract dialogues attributed to characters
            for name in names_in_chapter:
                pattern = re.compile(
                    rf"{re.escape(name)}[^，。！？]*?[：:]?\s*[""「](.+?)[""」]"
                )
                for dm in pattern.finditer(content):
                    dialogues.setdefault(name, []).append(dm.group(1)[:100])

            # Track co-occurrences (characters in same paragraph)
            paragraphs = content.split("\n")
            for para in paragraphs:
                chars_in_para = [n for n in names_in_chapter if n in para]
                for c1 in chars_in_para:
                    for c2 in chars_in_para:
                        if c1 != c2:
                            co_occurrences.setdefault(c1, Counter())[c2] += 1

        # Filter and build profiles
        profiles = []
        for name, count in name_counter.most_common(top_n):
            if count < min_mentions:
                continue
            top_cooccur = co_occurrences.get(name, Counter()).most_common(5)
            profiles.append({
                "name": name,
                "mention_count": count,
                "first_appearance": first_seen.get(name, 0),
                "sample_dialogues": (dialogues.get(name, []))[:5],
                "co_occurrences": [{"name": n, "count": c} for n, c in top_cooccur],
            })

        logger.info("Extracted %d character profiles", len(profiles))
        return profiles
