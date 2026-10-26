"""Quality filters: spam, boilerplate, markup, garbled encoding.

Every rule returns a reason code and is written to be conservative about text
that is legitimate but unusual:

  short proverbs / one-word queries  -> never dropped for length (only < MIN_ALNUM
                                        alphanumeric characters is "no content")
  poems with refrains                -> repetition rules only fire on long docs
  tables of numbers                  -> no letters is fine; they are language-neutral
  boilerplate inside a real document -> the LINES are stripped, the document stays

`check()` returns (reason | None, text, flags). `text` may differ from the input
only by stripped boilerplate lines.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

# --- tunables (all recorded in manifest.json) ---------------------------------
MIN_ALNUM = 3                 # fewer alphanumeric chars than this = no content
MAX_CHARS = 2_000_000         # single "document" larger than this is not text we want
FFFD_COUNT = 3                # U+FFFD occurrences that mean garbled
FFFD_RATIO = 0.005
MOJIBAKE_LEFT = 3             # leftover mojibake signatures after repair
BAD_DEV_MIN = 3               # structurally impossible Devanagari sequences ...
BAD_DEV_RATIO = 0.01          # ... and their share of Devanagari letters
MARKUP_TAGS = 3
MARKUP_RATIO = 0.05
URL_MAX = 3
RUN_CHAR = 10                 # same char repeated this many times
REPEAT_MIN_TOKENS = 20        # token-repetition rules need this many words
UNIQUE_TOKEN_RATIO = 0.25
TOP_TOKEN_SHARE = 0.4
DUP_LINE_MIN_LINES = 8
DUP_LINE_RATIO = 0.7
SYMBOL_RATIO = 0.3
SPAM_HITS = 2
BOILERPLATE_COVER = 0.4       # share of the line covered by boilerplate phrases
BOILERPLATE_COVER_MULTI = 0.25  # ... when two DIFFERENT boilerplate phrases are present

_FFFD = "�"
_MOJI = re.compile(r"(?:à¤|à¥|Ã.|â€)")
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]{0,200})?/?>|<!--.*?-->|<!DOCTYPE[^>]*>", re.S)
_ENTITY = re.compile(r"&(?:[A-Za-z]{2,8}|#\d{2,5});")
_TEMPLATE = re.compile(r"\{\{|\}\}|\[\[|\]\]|\{%|%\}|</?\w+\s*\{")
_CODEISH = re.compile(r"\b(?:function\s*\(|var\s+\w+\s*=|document\.(?:write|getElement)|window\.|<script|\$\(|!important)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_PHONEISH = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
_RUN = re.compile(r"(.)\1{%d,}" % (RUN_CHAR - 1))
_TOKEN = re.compile(r"(?:[^\W\d_]|[ऀ-ःऺ-ॏॕ-ॗॢॣ])+")
_BAD_DEV = re.compile(
    r"(?<![ऀ-ॿ])[ऀ-ःऺ-्॑-ॗॢॣ]"   # sign with no base letter
    r"|््|[ा-ौ][ा-ौ]")                                # double virama / two matras
_PLACEHOLDER = re.compile(r"^(?:null|none|nan|n/?a|undefined|nil|todo|tbd|test|lorem ipsum.*|\.+|-+|_+)$", re.I)

# Spam / scam / SEO phrases (casefolded substrings). English, Hindi, Hinglish.
SPAM_PHRASES = tuple(p.casefold() for p in """
click here|buy now|free download|limited offer|act now|call now|whatsapp us|earn money|work from home|make money
100% guaranteed|you have won|lottery winner|online casino|online betting|viagra|cheap price|discount code|promo code
loan approval|seo services|escort service|call girls|sex video|dating site|bitcoin investment|double your money
best rates|order now|subscribe now|visit our website|lorem ipsum|satta matka|matka result|kalyan matka
यहाँ क्लिक करें|यहां क्लिक करें|अभी खरीदें|मुफ्त डाउनलोड|मुफ़्त डाउनलोड|लॉटरी जीत|सट्टा मटका|मटका|कॉल करें|संपर्क करें
घर बैठे कमाएं|घर बैठे पैसे|पैसे कमाएं|फ्री में|वशीकरण|काला जादू|लव प्रॉब्लम|प्रॉब्लम सॉल्यूशन|डेटिंग|एस्कॉर्ट|कॉल गर्ल
ghar baithe|paise kamaye|kala jadu|vashikaran|love problem|solution baba|free mein
""".replace("\n", "|").split("|") if p.strip())

# Boilerplate line patterns.
_BOILER = re.compile("|".join([
    r"all rights reserved", r"copyright\s*©|©\s*\d{4}|\(c\)\s*\d{4}", r"privacy policy", r"terms (?:of (?:use|service)|(?:and|&) conditions)",
    r"cookie", r"subscribe", r"sign (?:up|in)", r"log ?in", r"read more", r"click here", r"share (?:on|this)", r"follow us",
    r"advertisement", r"sponsored", r"back to top", r"skip to (?:main )?content", r"^(?:home|about|contact)(?:\s*[|/»>-]\s*\w+)+$",
    r"सर्वाधिकार सुरक्षित", r"और पढ़ें", r"अधिक पढ़ें", r"यह भी पढ़ें", r"ये भी पढ़ें", r"विज्ञापन", r"फॉलो करें", r"शेयर करें",
    r"सब्सक्राइब", r"लॉग ?इन", r"हमसे जुड़ें", r"संबंधित खबरें", r"कुकी", r"गोपनीयता नीति", r"नियम (?:और|एवं) शर्तें",
]), re.I)


def _count_tags(text: str) -> tuple[int, int]:
    n = chars = 0
    for m in _TAG.finditer(text):
        n += 1
        chars += len(m.group(0))
    return n, chars


def _is_boiler_line(line: str) -> bool:
    """A line is boilerplate only if boilerplate phrases ARE the line.

    Mere containment is not enough: "cookie exchange parties" and "तीन विज्ञापन कर
    चुके हैं" are ordinary sentences (measured: 13 false drops with a containment rule).
    """
    hits = [m.group(0) for m in _BOILER.finditer(line)]
    if not hits:
        return False
    covered = sum(len(h) for h in hits) / max(1, len(line.strip()))
    distinct = len({h.casefold() for h in hits})
    return covered >= BOILERPLATE_COVER or (distinct >= 2 and covered >= BOILERPLATE_COVER_MULTI)


def check(text: str) -> tuple[str | None, str, list[str]]:
    flags: list[str] = []
    n = len(text)
    if n == 0:
        return "empty", text, flags
    if n > MAX_CHARS:
        return "too_long", text, flags

    # ---- garbled encoding ----------------------------------------------------
    fffd = text.count(_FFFD)
    if fffd >= FFFD_COUNT or fffd / n > FFFD_RATIO:
        return "garbled_encoding", text, flags
    if len(_MOJI.findall(text)) >= MOJIBAKE_LEFT:
        return "garbled_encoding", text, flags
    dev_letters = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    if dev_letters:
        bad = len(_BAD_DEV.findall(text))
        if bad >= BAD_DEV_MIN and bad / dev_letters >= BAD_DEV_RATIO:
            return "garbled_encoding", text, flags

    if _PLACEHOLDER.match(text.strip()):
        return "placeholder", text, flags
    alnum = sum(1 for ch in text if ch.isalnum() or unicodedata.category(ch)[0] == "M")
    if alnum < MIN_ALNUM:
        return "no_content", text, flags

    # ---- markup / code -------------------------------------------------------
    tags, tag_chars = _count_tags(text)
    if tags >= MARKUP_TAGS or tag_chars / n >= MARKUP_RATIO:
        return "markup", text, flags
    if len(_ENTITY.findall(text)) >= 3 or len(_TEMPLATE.findall(text)) >= 2 or _CODEISH.search(text):
        return "markup", text, flags

    # ---- boilerplate lines: strip from real documents, drop if nothing is left
    lines = text.split("\n")
    if any(_BOILER.search(ln) for ln in lines):
        kept = [ln for ln in lines if not _is_boiler_line(ln)]
        removed = len(lines) - len(kept)
        nonempty = [ln for ln in lines if ln.strip()]
        if removed:
            body = "\n".join(kept).strip()
            body_alnum = sum(1 for ch in body if ch.isalnum())
            if len(nonempty) and removed / len(nonempty) >= 0.5 or body_alnum < MIN_ALNUM:
                return "boilerplate", text, flags
            text = re.sub(r"\n{3,}", "\n\n", body)
            flags.append(f"boilerplate_lines_stripped:{removed}")
            lines = text.split("\n")
            n = len(text)

    # ---- spam ----------------------------------------------------------------
    low = text.casefold()
    urls = _URL.findall(text)
    phones = _PHONEISH.findall(text)
    spam_hits = sum(1 for p in SPAM_PHRASES if p in low)
    if spam_hits >= SPAM_HITS or (spam_hits >= 1 and (urls or phones)):
        return "spam", text, flags
    if len(urls) > URL_MAX or sum(len(u) for u in urls) / n > 0.4:
        return "spam", text, flags
    runs = sum(len(m.group(0)) for m in _RUN.finditer(text) if not m.group(0)[0] in "-=_.*~#─═ ")
    if runs / n > 0.3:
        return "spam", text, flags
    symbols = sum(1 for ch in text if unicodedata.category(ch) in ("So", "Sk", "Sm", "Cs", "Co"))
    if n >= 8 and symbols / n > SYMBOL_RATIO:
        return "spam", text, flags

    # ---- repetition (long documents only: refrains in poems must survive) -----
    toks = [t.casefold() for t in _TOKEN.findall(text)]
    if len(toks) >= REPEAT_MIN_TOKENS:
        freq = Counter(toks)
        uniq = len(freq) / len(toks)
        top = max(freq.values()) / len(toks)
        if uniq < UNIQUE_TOKEN_RATIO or top > TOP_TOKEN_SHARE:
            return "repetitive", text, flags
    nonempty = [ln.strip() for ln in lines if ln.strip()]
    if len(nonempty) >= DUP_LINE_MIN_LINES:
        if 1 - len(set(nonempty)) / len(nonempty) > DUP_LINE_RATIO:
            return "repetitive", text, flags

    return None, text, flags
