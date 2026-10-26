"""Cross-script "consonant skeleton" for contamination checks.

A Devanagari sentence and its Latin transliteration (ISO diacritics or casual
Hinglish spelling) have no characters in common, so normal matching cannot see
that they are the same text. We therefore map both scripts to one coarse
phonetic skeleton and compare *that*:

    यह घर बहुत बड़ा है      -> yhgrbhtbdh
    yah ghar bahut bada hai   -> yhgrbhtbdh
    yeh ghar bohot bada he    -> yhgrbhtbdh

Rules (lossy on purpose, so spelling variants collapse):
  * vowels are dropped; aspirates fold into their plain stop (kh->k, bh->b ...);
  * dental/retroflex pairs merge (t/ṭ, d/ḍ, n/ṇ, s/ś/ṣ); ड़/ढ़/ṛ -> d;
  * nasalisation (anusvara, chandrabindu, ṃ, m̐) -> n;
  * w->v, z->j, f->p, q->k, x->ks, c->k except 'ch' -> c; doubled letters collapse.
Similarity is then an ordinary MinHash Jaccard on skeleton 4-grams. Only
cross-script pairs are scored (same-script copies are caught by the other methods).
"""
from __future__ import annotations

import re
import unicodedata

_DEV = {
    # consonants
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n",
    "च": "c", "छ": "c", "ज": "j", "झ": "j", "ञ": "n",
    "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n",
    "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n",
    "प": "p", "फ": "p", "ब": "b", "भ": "b", "म": "m",
    "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "s", "ष": "s", "स": "s", "ह": "h",
    "क़": "k", "ख़": "k", "ग़": "g", "ज़": "j", "ड़": "d", "ढ़": "d",
    "फ़": "p", "य़": "y",
    # nasalisation
    "ँ": "n", "ं": "n",
}
_DEV_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
_ISO = str.maketrans({"ṛ": "d", "ṝ": "d", "ḍ": "d", "ṭ": "t", "ṇ": "n", "ṅ": "n", "ñ": "n",
                      "ś": "s", "ṣ": "s", "ṃ": "n", "ṁ": "n", "ḥ": "h", "ā": "a", "ī": "i", "ū": "u",
                      "ē": "e", "ō": "o", "ḷ": "l", "ṉ": "n"})
_LATIN_VOWELS = set("aeiouy")  # 'y' handled below: kept only before a vowel (य)


def _from_devanagari(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    out = []
    for ch in s:
        if ch in _DEV:
            out.append(_DEV[ch])
        elif ch in _DEV_DIGITS:
            out.append(_DEV_DIGITS[ch])
        elif "ऀ" <= ch <= "ॿ":
            continue                        # vowels, matras, virama, danda ...
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())          # English words inside Hindi text: handled by _from_latin below
    return "".join(out)


def _latin_word(w: str) -> str:
    w = re.sub(r"chh?", "C", w)              # ch / chh -> c
    w = re.sub(r"sh", "s", w)
    w = re.sub(r"([kgjtdpb])h", r"\1", w)   # aspirates (inside a word only: 'ek hai' must stay k,h)
    w = w.replace("c", "k").replace("C", "c").replace("q", "k").replace("x", "ks")
    w = w.replace("w", "v").replace("z", "j").replace("f", "p")
    return "".join(c for c in w if c not in "aeiou")


def _from_latin(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold().translate(_ISO)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(_latin_word(w) for w in re.findall(r"[a-z0-9]+", s))


def skeleton(text: str) -> str:
    dev = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    lat = sum(1 for c in text if c.isascii() and c.isalpha() or 0xC0 <= ord(c) <= 0x24F or 0x1E00 <= ord(c) <= 0x1EFF)
    sk = _from_devanagari(text) if dev > lat else _from_latin(text)
    return re.sub(r"(.)\1+", r"\1", sk)      # collapse doubled letters


def script_of(text: str) -> str:
    dev = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    lat = sum(1 for c in text if c.isalpha() and ord(c) < 0x250)
    if dev > lat:
        return "Deva"
    if lat > dev:
        return "Latn"
    return "none"
