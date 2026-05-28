"""Market extractor — mines the crawler DB for platform style profiles.

6-phase MapReduce pipeline:
    Phase 1: representative_selector — pick top-30 works per platform×category
    Phase 2: chapter feature extraction — NLP + local LLM
    Phase 3: work aggregator — fold per-chapter into per-work
    Phase 4: category aggregator + profile synthesizer (cloud LLM)
    Phase 5: holdout evaluator + Loader 2 hookup
    Phase 6: scheduler + incremental updates

See ``docs/MARKET_EXTRACTOR_PLAN.md`` for the full spec.
"""
from .dictionaries import (
    load_genre_dict, load_classical_words,
    load_internet_slang, load_idioms,
)

__all__ = [
    "load_genre_dict", "load_classical_words",
    "load_internet_slang", "load_idioms",
]
