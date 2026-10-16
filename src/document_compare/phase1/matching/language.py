"""Language profile for DE/EN regulatory documents (mostly DE).

The pipeline core is language-agnostic; only three touchpoints need
bilingual awareness:
1. suspended-hyphen stopwords (s04) — a line-wrap continuation is never a
   function word in either language, so the union is safe;
2. structural bucket prefixes (s15 index, redline renderer);
3. lexical anchor labels (s05) — already bilingual via its own regex.
"""
from __future__ import annotations

GERMAN_SUSPENDED_HYPHEN_STOPWORDS = {
    "und", "oder", "der", "die", "das", "des", "dem", "den",
    "ein", "eine", "einen", "einem", "einer", "von", "vom",
    "zu", "zum", "zur", "im", "in", "an", "am", "auf", "aus",
    "bei", "mit", "nach", "über", "vor", "für", "ohne", "gegen",
}

ENGLISH_SUSPENDED_HYPHEN_STOPWORDS = {
    "and", "or", "the", "of", "to", "in", "on", "at", "by",
    "for", "with", "from", "as", "is", "are", "be",
}

# Union on purpose: joining is only suppressed, never wrongly forced, and
# this avoids per-document language detection in the normalization hot path.
SUSPENDED_HYPHEN_STOPWORDS = (
    GERMAN_SUSPENDED_HYPHEN_STOPWORDS | ENGLISH_SUSPENDED_HYPHEN_STOPWORDS
)

# Prefixes that start a new structural bucket in the redline/index.
STRUCTURE_BUCKET_PREFIXES = (
    # German
    "Artikel ", "Anhang ", "Anlage ", "Kapitel ", "Abschnitt ", "Teil ", "Titel ",
    # English
    "Article ", "Annex ", "Chapter ", "Section ", "Part ", "Title ",
    # SEPA / scheme documentation common headings
    "Anlage 1 ", "Annex 1 ", "Kapitel 1 ", "Chapter 1 ",
)
