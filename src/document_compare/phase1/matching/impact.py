"""Deterministic German-first impact classification for paired source text."""

from difflib import SequenceMatcher
import re
import unicodedata

from document_compare.phase1.stages.s04_normalize import (
    DASH_TRANSLATION,
    QUOTE_TRANSLATION,
)

IMPACT_VERSION = "impact-1.0.0"

_BULLET = re.compile(r"^[•◦▪‣⁃·] *")
STATE_MARKERS = frozenset("□■☐☑☒✓✔✗✘●○▫")
_CURRENCY = re.compile(
    r"(?:EUR|USD|GBP|CHF|JPY|CNY|RMB|CAD|AUD|NZD|SEK|NOK|DKK|PLN|CZK|HUF|INR|"
    r"Euro|Dollar|Franken|Pfund|Yen|Yuan|Krone[n]?|Rupie[n]?|US\$|CA\$|AU\$|HK\$)(?![^\W\d_])",
    re.IGNORECASE,
)
_NUMERIC_OPERATORS = frozenset("/:-–x×")
_ADJACENT_MULTIPLICATION = re.compile(r"(?<=\d)\s*[xX×]\s*(?=\d)")
_SHARP_S = re.compile(r"ß(?=$|[bcdfghjklmnpqrstvwxyz])")
_DATE = r"(?:\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})"
_GENDER_SUFFIX = r"(?:[*:_]innen|/-?innen|Innen)"
_WORD = r"[^\W_]+"
_TOKEN = re.compile(
    rf"(?P<date>{_DATE})(?!\w|[.,]\d)"
    r"|(?P<number>(?:(?<!\w)-)?\d+(?:[.,]\d+)*(?!\w|[.,]\d))"
    r"|(?P<percent>(?i:vh|v\.h\.))(?!\w)"
    rf"|(?i:{_CURRENCY.pattern})"
    rf"|(?P<word>{_WORD}(?:{_GENDER_SUFFIX}(?!\w))?)"
    r"|(?P<punctuation>\S)"
)
_GENDER_END = re.compile(rf"(?<=\w){_GENDER_SUFFIX}$")
_DROPPED_PUNCTUATION = frozenset(",;:.!?()[]\"'/…-–")
_GERMAN_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")
_GERMAN_DECIMAL = re.compile(r"\d+,\d+")
_AMBIGUOUS_COMMA = re.compile(r"\d{1,3},\d{3}")
_NUMBER_WORDS = {
    word: f"#{value}"
    for value, variants in (
        (0, "null zero"), (1, "eins"), (2, "zwei two"), (3, "drei three"),
        (4, "vier four"), (5, "fünf five"), (6, "sechs six"), (7, "sieben seven"),
        (8, "acht eight"), (9, "neun nine"), (10, "zehn ten"), (11, "elf eleven"),
        (12, "zwölf twelve"), (20, "zwanzig twenty"), (30, "dreissig thirty"),
        (40, "vierzig forty"), (50, "fünfzig fifty"), (60, "sechzig sixty"),
        (70, "siebzig seventy"), (80, "achtzig eighty"), (90, "neunzig ninety"),
        (100, "hundert hundred"),
    )
    for word in variants.split()
}

_SIGNAL_WORDS = {
    "modal": frozenset(
        ("muss müssen musst soll sollen sollte sollten kann können könnte könnten "
         "müsste müssten musste mussten sollst solltest dürften durfte durften "
         "konnte konnten könne müsse solle dürfe "
         "darf dürfen dürfte braucht brauchen verpflichtet verpflichten berechtigt "
         "erforderlich zwingend gegebenenfalls ggf grundsätzlich must shall should "
         "may might can required mandatory optional permitted").split()
    ),
    "negation": frozenset(
        ("nicht kein keine keinen keinem keiner keines nie niemals weder nichts ohne "
         "not no never without neither nor none").split()
    ),
    "qualifier": frozenset(
        ("mindestens höchstens spätestens frühestens unverzüglich ausschliesslich "
         "nur ausnahmslos insbesondere jeweils least most latest only exclusively "
         "immediately").split()
    ),
    "condition": frozenset(
        ("wenn falls sofern soweit ausser ausgenommen vorbehaltlich sobald solange "
         "unless if except provided where").split()
    ),
    "date": frozenset(
        ("januar jänner februar märz april mai juni juli august september oktober "
         "november dezember january february march june july october december "
         "stichtag frist deadline").split()
    ),
}
_REFERENCE_WORDS = frozenset(
    ("§ art artikel abs absatz satz nr nummer buchst buchstabe ziff ziffer anhang "
     "anlage kapitel abschnitt tz textziffer rn randnummer article paragraph section "
     "annex chapter").split()
)
_ONE_SIDED_PRIORITY_SIGNALS = {"modal", "negation", "condition", "number", "date", "reference"}
_OPCODE_SIGNALS = {
    "insert": "added_text",
    "delete": "deleted_text",
    "replace": "replaced_text",
}


def _fold_glyphs(text: str) -> tuple[str, set[str]]:
    kinds: set[str] = set()
    composed = unicodedata.normalize("NFC", text)
    if composed != text:
        kinds.add("unicode_composition")
    # NFKC per character, except "other number" glyphs (superscripts, fractions,
    # circled digits): folding "5¹" into "51" would hide a footnote/value change.
    compatible = "".join(
        char if unicodedata.category(char) == "No" else unicodedata.normalize("NFKC", char)
        for char in composed
    )
    compatible = unicodedata.normalize("NFC", compatible)
    if compatible != composed:
        if " ".join(compatible.split()) != " ".join(composed.split()):
            kinds.add("ligature_or_compatibility_form")
        if re.findall(r"\s+", compatible) != re.findall(r"\s+", composed):
            kinds.add("whitespace")
    visible = "".join(char for char in compatible if unicodedata.category(char) != "Cf")
    if visible != compatible:
        kinds.add("invisible_character")
    translated = visible.translate(QUOTE_TRANSLATION).translate(DASH_TRANSLATION)
    if translated != visible:
        kinds.add("quote_or_dash_variant")
    spaced = " ".join(translated.split())
    if spaced != translated:
        kinds.add("whitespace")
    folded = _BULLET.sub("", spaced, count=1)
    if (
        spaced.startswith("- ") and " ".join(visible.split()).startswith("- ")
        and not _quantity_start(spaced[2:])
    ):
        folded = spaced[2:]
    if folded != spaced:
        kinds.add("bullet_glyph")
    return folded, kinds


def _quantity_start(text: str) -> bool:
    return bool(text) and (
        text[0].isdigit() or text[0] == "%"
        or unicodedata.category(text[0]) == "Sc" or _CURRENCY.match(text) is not None
    )


def raw_glyph_signature(text: str) -> tuple[str, ...]:
    """Keep the ordered raw digits, signs and state marks for suppression checks."""
    return tuple(char for char in text if char.isdigit() or char in "+-−" or char in STATE_MARKERS)


def glyph_fold(text: str) -> str:
    """Fold glyph variants and extraction whitespace without folding case."""
    return _fold_glyphs(text)[0]


# Line-break hyphenation from PDF extraction: "Risikotragfähigkeitskon- zepte".
# A suspended hyphen before a conjunction ("Ein- und Ausgang") is real text.
_SUSPENDED_HYPHEN_NEXT = (
    "und oder bis sowie bzw beziehungsweise respektive and or to"
).split()
_LINE_BREAK_HYPHEN = re.compile(
    rf"(?<=[^\W\d_])- (?!(?:{'|'.join(_SUSPENDED_HYPHEN_NEXT)})\b)(?=[a-zäöüß])"
)
# Fixed German abbreviations with one unambiguous expansion (inflected forms
# such as "sog." or "o. g." are left alone).
_ABBREVIATIONS = tuple(
    (re.compile(pattern), expansion)
    for pattern, expansion in (
        (r"\b[zZ]\. ?B\.", "zum Beispiel"),
        (r"\b[dD]\. ?h\.", "das heißt"),
        (r"\b[uU]\. ?a\.(?= |$)", "unter anderem"),
        (r"\b[iI]\. ?d\. ?R\.", "in der Regel"),
        (r"\b[uU]\. ?U\.", "unter Umständen"),
        (r"\b[zZ]\. ?T\.", "zum Teil"),
        (r"\bbzw\.", "beziehungsweise"),
        (r"\bggf\.", "gegebenenfalls"),
        (r"\bgem\.", "gemäß"),
        (r"\bvgl\.", "vergleiche"),
        (r"\binkl\.", "inklusive"),
        (r"\busw\.", "und so weiter"),
        (r"\bAbs\.", "Absatz"),
        (r"\bNr\.", "Nummer"),
        (r"\bTz\. ?", "Textziffer "),
        (r"\bRn\. ?", "Randnummer "),
    )
)
# Leading enumeration labels "a)", "(b)", "iv)", "3)"; "1." is not stripped
# because it can be the day of a date ("1. Januar").
_LIST_LABEL = re.compile(r"^\(?(?:[a-zA-Z]{1,2}|[ivxIVX]{1,4}|\d{1,3})\) +")


def _editorial_text(text: str) -> tuple[str, set[str]]:
    """Fold extraction hyphenation and fixed abbreviations before tokenizing."""
    kinds: set[str] = set()
    joined = _LINE_BREAK_HYPHEN.sub("", text)
    if joined != text:
        kinds.add("hyphenation")
    expanded = joined
    for pattern, expansion in _ABBREVIATIONS:
        expanded = pattern.sub(expansion, expanded)
    expanded = " ".join(expanded.split())
    if expanded != " ".join(joined.split()):
        kinds.add("abbreviation")
    return expanded, kinds


def _strip_shared_labels(old: str, new: str) -> tuple[str, str, set[str]]:
    old_label, new_label = _LIST_LABEL.match(old), _LIST_LABEL.match(new)
    if not (old_label and new_label):
        return old, new, set()
    kinds = {"list_label"} if old_label[0].strip() != new_label[0].strip() else set()
    return old[old_label.end():], new[new_label.end():], kinds


def _number_token(raw: str) -> str:
    unsigned = raw.removeprefix("-")
    value = "".join(
        str(unicodedata.decimal(char)) if char.isdecimal() else char
        for char in unsigned
    )
    if _GERMAN_GROUPED.fullmatch(value):
        value = value.replace(".", "")
    elif not (
        value.isdecimal()
        or (_GERMAN_DECIMAL.fullmatch(value) and not _AMBIGUOUS_COMMA.fullmatch(value))
    ):
        return raw
    integer, _, fraction = value.partition(",")
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    sign = "-" if raw.startswith("-") else ""
    return f"#{sign}{integer}.{fraction}" if fraction else f"#{sign}{integer}"


def _editorial_parts(folded: str) -> tuple[list[str], list[str], list[tuple[int, str]]]:
    tokens: list[str] = []
    originals: list[str] = []
    punctuation: list[tuple[int, str]] = []
    separated = _ADJACENT_MULTIPLICATION.sub(lambda match: f" {match[0].strip()} ", folded)
    parts = []
    for match in _TOKEN.finditer(separated):
        raw = match[0]
        if match.lastgroup == "date":
            token = f"@date:{raw}"
        elif match.lastgroup == "number":
            token = _number_token(raw)
        elif match.lastgroup == "percent":
            token = "%"
        else:
            word = _editorial_word(raw)
            token = "%" if word == "prozent" else _NUMBER_WORDS.get(word, word)
        parts.append((raw, token))

    for index, (raw, token) in enumerate(parts):
        if raw in _DROPPED_PUNCTUATION:
            between_numbers = (
                token in _NUMERIC_OPERATORS and 0 < index < len(parts) - 1
                and _is_number(parts[index - 1][1]) and _is_number(parts[index + 1][1])
            )
            quantity_sign = (
                raw == "-" and index + 1 < len(parts)
                and _quantity_start(parts[index + 1][0])
            )
            if not (between_numbers or quantity_sign):
                punctuation.append((len(tokens), raw))
                continue
        originals.append(raw)
        tokens.append(token)
    return tokens, originals, punctuation


def _editorial_word(raw: str) -> str:
    return _SHARP_S.sub("ss", _GENDER_END.sub("", raw).lower())


def editorial_tokens(text: str) -> list[str]:
    """Tokenize folded text, keeping numeric, date and reference evidence."""
    return _editorial_parts(_editorial_text(glyph_fold(text))[0])[0]


def _editorial_kinds(
    old_parts: tuple[list[str], list[str], list[tuple[int, str]]],
    new_parts: tuple[list[str], list[str], list[tuple[int, str]]],
    old: str,
    new: str,
) -> set[str]:
    tokens, old_originals, old_punctuation = old_parts
    _, new_originals, new_punctuation = new_parts
    kinds: set[str] = set()
    if old_punctuation != new_punctuation:
        kinds.add("punctuation")
    if re.findall(r"\s+", old) != re.findall(r"\s+", new):
        kinds.add("whitespace")
    for token, left, right in zip(tokens, old_originals, new_originals):
        if left == right:
            continue
        left_word = _GENDER_END.sub("", left)
        right_word = _GENDER_END.sub("", right)
        if left[len(left_word):] != right[len(right_word):]:
            kinds.add("gender_form")
        left_lower, right_lower = left_word.lower(), right_word.lower()
        if left_word != right_word and left_lower == right_lower:
            kinds.add("letter_case")
        if (
            left_lower != right_lower
            and _editorial_word(left) == _editorial_word(right)
        ):
            kinds.add("orthography")
        if (token.startswith("#") or token == "%") and left_lower != right_lower:
            kinds.add("number_format")
    return kinds or {"orthography"}


def _is_number(token: str) -> bool:
    return (
        token.startswith(("#", "@date:"))
        or token == "%"
        or token.removeprefix("-")[:1].isdigit()
    )


def _changed_signals(tokens: list[str], start: int, end: int) -> set[str]:
    changed = tokens[start:end]
    signals = {name for name, words in _SIGNAL_WORDS.items() if words.intersection(changed)}
    for index in range(start, end):
        token = tokens[index]
        number = _is_number(token)
        if number or (
            token in _NUMERIC_OPERATORS and 0 < index < len(tokens) - 1
            and _is_number(tokens[index - 1]) and _is_number(tokens[index + 1])
        ) or (
            token in {"+", "-"} and index + 1 < len(tokens)
            and (_is_number(tokens[index + 1]) or _quantity_start(tokens[index + 1]))
        ):
            signals.add("number")
        if token in STATE_MARKERS:
            signals.add("state_marker")
        if token.startswith("@date:"):
            signals.add("date")
        # A citation label can precede a letter suffix through its number,
        # for example "Abs. 2 a". Otherwise it must be immediately adjacent.
        letter = len(token) == 1 and token.isalpha()
        preceding = index - 1
        if letter and preceding >= 0 and _is_number(tokens[preceding]):
            preceding -= 1
        if (number or letter) and _REFERENCE_WORDS.intersection(
            tokens[max(0, preceding):index + 2]
        ):
            signals.add("reference")
        if (
            token == "at"
            and index + 1 < len(tokens)
            and tokens[index + 1] in {"least", "most", "latest"}
        ):
            signals.add("qualifier")
    return signals


def _result(
    level: str, priority: str, *, kinds: tuple[str, ...] | set[str] = (),
    signals: set[str] | None = None,
) -> dict:
    return {
        "version": IMPACT_VERSION,
        "level": level,
        "priority": priority,
        "kinds": sorted(kinds),
        "signals": sorted(signals or ()),
        "basis": "deterministic_rule",
        "language_profile": "de-en",
    }


def classify_impact(old_text: str | None, new_text: str | None) -> dict:
    """Classify impact independently of match certainty and disposition."""
    if old_text == new_text:
        return _result("non_content", "low", kinds=("identical",))

    old, new = old_text or "", new_text or ""
    old_present, new_present = bool(old.strip()), bool(new.strip())
    old_folded, old_kinds = _fold_glyphs(old)
    new_folded, new_kinds = _fold_glyphs(new)
    one_sided = old_present != new_present
    if not one_sided and old_folded == new_folded:
        return _result("non_content", "low", kinds=old_kinds | new_kinds or {"whitespace"})

    abbreviation_use = [
        tuple(len(pattern.findall(text)) for pattern, _ in _ABBREVIATIONS)
        for text in (old_folded, new_folded)
    ]
    old_folded, old_editorial = _editorial_text(old_folded)
    new_folded, new_editorial = _editorial_text(new_folded)
    text_kinds = old_editorial | new_editorial
    if abbreviation_use[0] == abbreviation_use[1]:
        text_kinds.discard("abbreviation")
    if not one_sided:
        old_folded, new_folded, label_kinds = _strip_shared_labels(old_folded, new_folded)
        text_kinds |= label_kinds
    old_parts = _editorial_parts(old_folded)
    new_parts = _editorial_parts(new_folded)
    old_tokens, new_tokens = old_parts[0], new_parts[0]
    if one_sided:
        tokens = new_tokens if new_present else old_tokens
        signals = _changed_signals(tokens, 0, len(tokens))
        signals.add("added_text" if new_present else "deleted_text")
        word_count = sum(
            bool(re.search(_WORD, match[0]))
            for match in _TOKEN.finditer(new_folded if new_present else old_folded)
        )
        priority = "high" if word_count <= 40 and signals & _ONE_SIDED_PRIORITY_SIGNALS else "normal"
        return _result("content", priority, signals=signals)
    elif old_tokens == new_tokens:
        kinds = _editorial_kinds(old_parts, new_parts, old_folded, new_folded)
        if text_kinds:
            kinds = (kinds - {"orthography"}) | text_kinds if kinds == {"orthography"} else kinds | text_kinds
        return _result("editorial", "low", kinds=kinds)
    else:
        signals = set()
        matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            signals.add(_OPCODE_SIGNALS[tag])
            signals.update(_changed_signals(old_tokens, old_start, old_end))
            signals.update(_changed_signals(new_tokens, new_start, new_end))

    priority = "high" if signals.difference(_OPCODE_SIGNALS.values()) else "normal"
    return _result("content", priority, signals=signals)
