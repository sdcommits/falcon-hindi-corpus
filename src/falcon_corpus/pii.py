"""PII redaction: Indian phone numbers, emails, Aadhaar, PAN.

Placeholders: <PHONE> <EMAIL> <AADHAAR> <PAN>.

Matching runs on an "ascii view" of the text in which every Unicode decimal digit
(Devanagari ०-९ included) is replaced by its ASCII digit, one character for one
character, so offsets line up with the original and `९८७६५ ४३२१०` is caught.

Precision matters as much as recall: "Numbers that are not PII must survive".
Hence phones are matched by group structure (10 digits, 5+5, 3+3+4 ...), never as
"any run of digits", so a table row like `70 80 90 60 50` is untouched, as are
years, prices (`1,00,000`), PIN codes and 10-digit numbers that cannot be a
mobile (first digit 1-5).

Decisions that trade recall for precision are listed in DECISIONS.md:
  * bare 12-digit numbers count as Aadhaar only with a Verhoeff-valid checksum or
    an Aadhaar keyword nearby; grouped 4-4-4 numbers are always redacted.
  * PAN is matched on format alone (AAAAA9999A), without checking the 4th letter.
"""
from __future__ import annotations

import re
import unicodedata

# ---- Verhoeff checksum (Aadhaar's last digit) --------------------------------
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
      [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
      [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


# ---- patterns (applied to the ASCII view) -------------------------------------
_SEP = r"[ .-]"
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
    r"|(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+\s?[\[(]\s?(?:at|AT)\s?[\])]\s?[A-Za-z0-9-]+"
    r"(?:\s?[\[(]?\s?(?:dot|DOT)\s?[\])]?\s?[A-Za-z0-9-]+)+")

_MOBILE_BODY = "|".join([
    r"[6-9]\d{9}", rf"[6-9]\d{{4}}{_SEP}\d{{5}}", rf"[6-9]\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{4}}",
    rf"[6-9]\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{3}}", rf"[6-9]\d{{3}}{_SEP}\d{{6}}",
    rf"[6-9]\d{_SEP}\d{{4}}{_SEP}\d{{4}}", rf"[6-9]\d{{2}}{_SEP}\d{{7}}"])
_CC = rf"(?:(?:\+|00)\s?91{_SEP}?|91{_SEP}|\(\+?91\)\s?)"
# A '.' next to a digit means a decimal number, not a phone; a ',' is just a list separator.
_L = r"(?<!\d)(?<!\d\.)"
_R = r"(?!\d)(?!\.\d)"
_MOBILE = re.compile(
    rf"{_L}(?:{_CC}|91(?=[6-9]\d{{9}}(?!\d)))?(?:\(0\)\s?|0{_SEP}?)?(?:{_MOBILE_BODY}){_R}")
# STD code: with trunk 0 (0172, 011, (0172)) or, after +91, without it (+91 22 ...).
_LANDLINE = re.compile(
    rf"{_L}(?:{_CC}0?\d{{2,4}}|\(0\d{{2,4}}\)|0\d{{2,4}}){_SEP}?(?:\d{{6,8}}|\d{{3,4}}{_SEP}\d{{3,4}}){_R}")
_TOLLFREE = re.compile(rf"{_L}1800{_SEP}?\d{{2,3}}{_SEP}?\d{{3,4}}{_R}")
_AADHAAR = re.compile(r"(?<!\d)(?<!\d[ -])[2-9]\d{3}([ -]?)\d{4}\1\d{4}(?!\d)(?![ -]\d)")
_AADHAAR_CTX = re.compile(r"aadh?a+r|adhaar|\buid(?:ai)?\b|आधार", re.I)
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")


def _ascii_view(text: str) -> str:
    if text.isascii():
        return text
    out = []
    for ch in text:
        if ch.isascii():
            out.append(ch)
        else:
            d = unicodedata.digit(ch, None) if unicodedata.category(ch) == "Nd" else None
            out.append(str(d) if d is not None else ch)
    return "".join(out)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_ok(span: str) -> bool:
    d = _digits(span)
    if d.startswith("0091"):
        d = d[4:]
    elif d.startswith("91") and len(d) in (12, 13):
        d = d[2:]
    if d.startswith("1800"):
        return len(d) in (10, 11)  # toll-free: 1800 + 6 or 7 digits
    if len(d) == 11 and d[0] == "0":
        return True                # trunk 0 + 10 digits (mobile or STD landline)
    return len(d) == 10


def find_spans(text: str) -> list[tuple[int, int, str]]:
    """Non-overlapping (start, end, kind) spans, priority email > aadhaar > pan > phone."""
    v = _ascii_view(text)
    cand: list[tuple[int, int, str, int]] = []   # start, end, kind, priority
    for m in _EMAIL.finditer(v):
        cand.append((m.start(), m.end(), "EMAIL", 0))
    for m in _AADHAAR.finditer(v):
        grouped = bool(m.group(1))
        ctx = bool(_AADHAAR_CTX.search(v[max(0, m.start() - 40):m.start()]))
        if grouped or ctx or verhoeff_valid(_digits(m.group(0))):
            cand.append((m.start(), m.end(), "AADHAAR", 1))
    for m in _PAN.finditer(v):
        cand.append((m.start(), m.end(), "PAN", 2))
    for rx, pri in ((_MOBILE, 3), (_LANDLINE, 3), (_TOLLFREE, 3)):
        for m in rx.finditer(v):
            if _phone_ok(m.group(0)):
                cand.append((m.start(), m.end(), "PHONE", pri))
    # Resolve overlaps: lowest priority number wins; on a tie the longer span wins.
    cand.sort(key=lambda c: (c[3], -(c[1] - c[0]), c[0]))
    taken: list[tuple[int, int, str]] = []
    for s, e, kind, _ in cand:
        if all(e <= ts or s >= te for ts, te, _ in taken):
            taken.append((s, e, kind))
    return sorted(taken)


def redact(text: str) -> tuple[str, dict[str, int]]:
    spans = find_spans(text)
    if not spans:
        return text, {}
    counts: dict[str, int] = {}
    out, pos = [], 0
    for s, e, kind in spans:
        out.append(text[pos:s])
        out.append(f"<{kind}>")
        counts[kind] = counts.get(kind, 0) + 1
        pos = e
    out.append(text[pos:])
    return "".join(out), counts
