"""Language labelling: exactly one of  hi | hi-Latn | en | other.

Labels shipped with the source are never read. The decision uses only the text:

1. Script mix: share of Devanagari among letters.
2. Devanagari-dominant text is `hi` (English words mixed in are fine) unless
   there is POSITIVE evidence for Marathi/Nepali, i.e. words that are not Hindi.
   Default-to-hi is deliberate: proverbs, poems and names have no function words
   and would otherwise be lost.
3. Latin-dominant text: romanised-Hindi function words decide `hi-Latn`; their
   share must be high enough that an English sentence quoting one Hindi word
   stays `en`.
4. No letters at all (tables of numbers): `other`, reason `no_letters`; the
   pipeline treats these as language-neutral, not as errors.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .lexicons import EN_STOP, HI_DEV, HI_LATN_STRONG, HI_LATN_WEAK, MR_DEV, NE_DEV, OTHER_LATIN

# Tunables (also recorded in manifest.json).
DEV_SHARE_HI = 0.5          # >= this share of letters in Devanagari => Devanagari text
DEV_SHARE_MIXED = 0.3       # Latin-heavy text still `hi` if this much is Devanagari
HI_LATN_MIN_SHARE = 0.10    # weighted romanised-Hindi tokens / Latin tokens
OTHER_SCRIPT_MAX = 0.30     # more non-Latin/non-Devanagari letters than this => other

# Letters PLUS Devanagari combining marks. Plain \w drops the marks (they are not
# alphanumeric), which shreds Hindi words into one-letter fragments.
_TOKEN = re.compile(r"(?:[^\W\d_]|[\u0900-\u0903\u093a-\u094f\u0955-\u0957\u0962\u0963])+")


# Hindi verb / adjective endings that English words (almost) never have.
# (-enge/-ega are NOT here: challenge, avenge, omega. Those forms are in the word list.)
_HI_SUFFIX = re.compile(r".{3,}(?:iye|iyega|aiye|ungi|unga|wala|wali|vala|vali|waala|waali)$")


def _hi_latn_hit(tok: str) -> bool:
    """Strong romanised-Hindi evidence: listed word, inflection, or compound."""
    if tok in HI_LATN_STRONG:
        return True
    if tok not in EN_STOP and len(tok) >= 6 and _HI_SUFFIX.match(tok):
        return True
    # compounds written as one word: kardo, hatado, bandkaro ...
    for i in range(2, len(tok) - 1):
        a, b = tok[:i], tok[i:]
        # both halves >= 3 letters (or a 3+ letter head + short tail): 'ka'+'de' must not fire on "Kade"
        if len(a) >= 3 and ((a in HI_LATN_STRONG and (b in HI_LATN_STRONG or b in HI_LATN_WEAK))
                            or (a in HI_LATN_WEAK and b in HI_LATN_STRONG and len(b) >= 3)):
            return True
    return False


@dataclass(frozen=True)
class LangResult:
    lang: str      # hi | hi-Latn | en | other
    why: str       # short machine-readable reason


def _script_counts(text: str) -> tuple[int, int, int]:
    dev = lat = oth = 0
    for ch in text:
        o = ord(ch)
        if 0x0900 <= o <= 0x097F or 0xA8E0 <= o <= 0xA8FF:
            cat = unicodedata.category(ch)
            if cat[0] in "LM":
                dev += 1
        elif ch.isalpha():
            if o < 0x250 or 0x1E00 <= o <= 0x1EFF:
                lat += 1
            else:
                oth += 1
    return dev, lat, oth


def identify(text: str) -> LangResult:
    dev, lat, oth = _script_counts(text)
    letters = dev + lat + oth
    if letters == 0:
        return LangResult("other", "no_letters")
    if oth / letters > OTHER_SCRIPT_MAX:
        return LangResult("other", "other_script")

    tokens = [t.casefold() for t in _TOKEN.findall(text)]
    dev_tokens = [t for t in tokens if "ऀ" <= t[0] <= "ॿ"]

    if dev / letters >= DEV_SHARE_HI:
        mr = sum(t in MR_DEV for t in dev_tokens)
        ne = sum(t in NE_DEV for t in dev_tokens)
        hi = sum(t in HI_DEV for t in dev_tokens)
        if mr >= 2 and mr > hi:
            return LangResult("other", f"marathi_markers={mr}")
        if ne >= 2 and ne > hi:
            return LangResult("other", f"nepali_markers={ne}")
        return LangResult("hi", f"devanagari_share={dev / letters:.2f}")

    # Latin-dominant (or mixed) from here on.
    if dev_tokens and (dev / letters >= DEV_SHARE_MIXED or any(t in HI_DEV for t in dev_tokens)):
        return LangResult("hi", f"mixed_devanagari_share={dev / letters:.2f}")

    lat_tokens = [t for t in tokens if t[0] < "ɐ"]
    n = len(lat_tokens)
    if n == 0:
        return LangResult("other", "no_latin_tokens")
    strong = sum(_hi_latn_hit(t) for t in lat_tokens)
    weak = sum(t in HI_LATN_WEAK for t in lat_tokens)
    if strong >= 1:
        share = (strong + 0.4 * weak) / n
        if share >= HI_LATN_MIN_SHARE:
            return LangResult("hi-Latn", f"hi_latn_share={share:.2f}")
    en = sum(t in EN_STOP for t in lat_tokens)
    ot = sum(t in OTHER_LATIN for t in lat_tokens)
    if ot >= 2 and ot > en:
        return LangResult("other", f"other_latin_language stop={ot} en={en}")
    return LangResult("en", f"en_stop={en}/{n}")
