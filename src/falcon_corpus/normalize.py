"""Text normalization.

Two different outputs, on purpose:

normalize_text(s)  -> the text we STORE. Conservative: it must stay natural
                      training text (case, punctuation, digits kept).
match_key(s)       -> the string we COMPARE (dedup, contamination). Aggressive:
                      anything a human would call "the same text" maps to the
                      same key.

Devanagari-specific points handled here (all measured on the data, see
DECISIONS.md):
  * NFC: precomposed nukta letters (U+0958..095F, e.g. क़) are composition
    exclusions, so NFC turns them into base + nukta (U+093C). Both spellings
    therefore store identically.
  * ZWJ/ZWNJ next to Devanagari are layout hints, not content: stripped.
    Elsewhere (emoji sequences) they are kept in stored text.
  * ASCII '|' used as a danda at end of line becomes '।'; space before a danda
    is removed (UD treebanks are pre-tokenised: "है ।").
  * Mojibake (UTF-8 bytes decoded as cp1252/latin-1) is repaired when the
    repair yields Devanagari; otherwise the garbled-encoding filter drops it.
"""
from __future__ import annotations

import re
import unicodedata

DEVANAGARI = re.compile(r"[ऀ-ॿ꣠-ꣿ]")

# Invisible / layout-only code points removed from stored text.
_INVISIBLE = dict.fromkeys(
    [0x200B, 0xFEFF, 0x2060, 0x00AD, 0x180E, 0x200E, 0x200F, 0x034F,
     *range(0x202A, 0x202F), *range(0x2066, 0x206A), *range(0xFE00, 0xFE0F)], None)
_NEWLINES = dict.fromkeys([0x0B, 0x0C, 0x85, 0x2028, 0x2029], "\n")

_MOJIBAKE = re.compile(r"(?:à¤|à¥|Ã.|â€|Â[ -¿])")
_SPACE_BEFORE_DANDA = re.compile(r"[ \t]+([।॥])")
_PIPE_DANDA = re.compile(r"(?<=[ऀ-ॿ])[ \t]*\|\|[ \t]*(?=$)", re.M)
_PIPE_DANDA1 = re.compile(r"(?<=[ऀ-ॿ])[ \t]*\|[ \t]*(?=$)", re.M)
_SPACE_BEFORE_PUNCT = re.compile(r"(?<=[\w\)\"'])[ \t]+([.,!?;])(?=\s|$)")
_HSPACE = re.compile(r"[ \t]+")


def _lenient_cp1252_bytes(s: str) -> bytes:
    """Invert a browser-style windows-1252 decode.

    Python's cp1252 codec refuses the five undefined bytes (0x81 0x8D 0x8F 0x90
    0x9D), but Hindi mojibake is full of them (0x8D is the tail byte of virama
    '्' = E0 A5 8D). Browsers map them to the C1 control with the same value, so
    we do too. Raises UnicodeEncodeError on any char that cannot be a byte.
    """
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) < 0x100:
                out.append(ord(ch))
            else:
                raise
    return bytes(out)


def repair_mojibake(s: str) -> tuple[str, bool]:
    """UTF-8 text that was decoded as cp1252/latin-1 -> original text."""
    if not _MOJIBAKE.search(s):
        return s, False
    try:
        fixed = _lenient_cp1252_bytes(s).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s, False
    if fixed != s and not _MOJIBAKE.search(fixed):
        return fixed, True
    return s, False


def _strip_joiners(s: str) -> str:
    """Drop ZWNJ/ZWJ when they touch Devanagari; keep them in emoji etc."""
    if "‌" not in s and "‍" not in s:
        return s
    out = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch in "‌‍":
            prev = s[i - 1] if i else ""
            nxt = s[i + 1] if i + 1 < n else ""
            if (prev and DEVANAGARI.match(prev)) or (nxt and DEVANAGARI.match(nxt)):
                continue
        out.append(ch)
    return "".join(out)


def normalize_text(s: str) -> tuple[str, set[str]]:
    """Return (stored_text, flags). Flags name every non-trivial change."""
    flags: set[str] = set()
    orig = s
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates from broken JSON escapes
        s = s.encode("utf-8", "replace").decode("utf-8")
        flags.add("lone_surrogate")
    s, fixed = repair_mojibake(s)
    if fixed:
        flags.add("mojibake_repaired")
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n").translate(_NEWLINES)
    before = s
    s = s.translate(_INVISIBLE)
    s = _strip_joiners(s)
    if s != before:
        flags.add("invisible_removed")
    # other control chars -> space; unicode spaces (NBSP, thin, ideographic ...) -> space
    s = "".join(
        "\n" if ch == "\n" else
        ("\t" if ch == "\t" else
         (" " if (unicodedata.category(ch) in ("Cc", "Zs", "Zl", "Zp")) else ch))
        for ch in s)
    # horizontal whitespace runs: one space (a tab survives as a tab: tables)
    s = _HSPACE.sub(lambda m: "\t" if "\t" in m.group(0) else " ", s)
    s = "\n".join(line.strip(" \t") for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s).strip("\n")
    # Devanagari sentence punctuation
    s = _PIPE_DANDA.sub("॥", s)
    s = _PIPE_DANDA1.sub("।", s)
    s = _SPACE_BEFORE_DANDA.sub(r"\1", s)
    s = _SPACE_BEFORE_PUNCT.sub(r"\1", s)
    if s != orig:
        flags.add("changed")
    return s, flags


# --------------------------------------------------------------------------
# match key
# --------------------------------------------------------------------------

def match_key(s: str) -> str:
    """Canonical form for equality tests. Not for training use.

    NFKC + casefold; drop everything that is not a letter, mark or digit
    (whitespace, punctuation, symbols, invisibles); any Unicode digit -> ASCII;
    Devanagari: drop nukta, fold chandrabindu into anusvara; Latin: strip accents.
    """
    s = unicodedata.normalize("NFKC", s).casefold()
    out = []
    for ch in s:
        o = ord(ch)
        cat = unicodedata.category(ch)
        c0 = cat[0]
        if c0 == "N":
            d = unicodedata.digit(ch, None)
            out.append(str(d) if d is not None else ch)
        elif c0 == "L":
            # Latin accents: é -> e (only below U+0250; never touches Indic letters)
            out.append(unicodedata.normalize("NFD", ch)[0] if o < 0x250 else ch)
        elif c0 == "M":
            if o == 0x093C or o in (0x200C, 0x200D):      # nukta
                continue
            if o == 0x0901:                                # chandrabindu -> anusvara
                out.append("ं"); continue
            if 0x0300 <= o <= 0x036F:                      # Latin combining accents
                continue
            out.append(ch)
        # everything else (P*, S*, Z*, C*) is dropped
    return "".join(out)
