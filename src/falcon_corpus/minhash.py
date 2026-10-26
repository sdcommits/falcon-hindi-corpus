"""MinHash signatures and a banded LSH index (deterministic, numpy only).

Shingles are character n-grams of the *match key* (see normalize.match_key), so
spacing, punctuation, case and Unicode variants never affect similarity.

Estimated Jaccard = fraction of equal signature slots (std. error ~ 0.04 at
J=0.8 with 128 slots). Slots are stored truncated to 32 bits.
"""
from __future__ import annotations

import base64

import numpy as np

NUM_PERM = 128
NGRAM = 5
BANDS = 32          # 32 bands x 4 rows: candidate probability 0.99 at J=0.6, 0.56 at J=0.4
ROWS = NUM_PERM // BANDS
_SEED = 20240611
_U = np.uint64


def _make_params():
    rs = np.random.RandomState(_SEED)          # legacy RandomState: stream is frozen across numpy versions
    a = rs.randint(1, 2**62, size=NUM_PERM, dtype=np.uint64) * _U(2) + _U(1)
    b = rs.randint(0, 2**62, size=NUM_PERM, dtype=np.uint64)
    return a, b


_A, _B = _make_params()


def _splitmix64(x: np.ndarray) -> np.ndarray:
    x = x + _U(0x9E3779B97F4A7C15)
    x = (x ^ (x >> _U(30))) * _U(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> _U(27))) * _U(0x94D049BB133111EB)
    return x ^ (x >> _U(31))


def shingle_hashes(key: str, n: int = NGRAM) -> np.ndarray:
    """64-bit hashes of every character n-gram (the whole key if shorter than n)."""
    if not key:
        return np.zeros(1, dtype=np.uint64)
    cp = np.frombuffer(key.encode("utf-32-le"), dtype=np.uint32).astype(np.uint64)
    if len(cp) < n:
        n = len(cp)
    m = len(cp) - n + 1
    h = np.zeros(m, dtype=np.uint64)
    mult = 1
    for j in range(n):
        h += cp[j:j + m] * _U(mult)          # array * scalar wraps mod 2**64 silently
        mult = (mult * 1000003) % (1 << 64)  # python int: no numpy scalar-overflow warning
    return _splitmix64(h)


def signature(key: str, n: int = NGRAM) -> np.ndarray:
    """uint32[NUM_PERM] MinHash signature."""
    h = shingle_hashes(key, n)
    sig = np.full(NUM_PERM, np.iinfo(np.uint64).max, dtype=np.uint64)
    step = 2048
    for i in range(0, len(h), step):
        blk = h[i:i + step]
        sig = np.minimum(sig, (_A[:, None] * blk[None, :] + _B[:, None]).min(axis=1))
    return (sig & _U(0xFFFFFFFF)).astype(np.uint32)


def sig_to_str(sig: np.ndarray) -> str:
    return base64.b64encode(sig.astype("<u4").tobytes()).decode("ascii")


def sig_from_str(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype="<u4")


def est_jaccard(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.count_nonzero(a == b)) / len(a)


class LSHIndex:
    """Banded LSH over signatures. Items are (id, signature); query returns scored candidates."""

    def __init__(self):
        self._buckets: dict[tuple[int, bytes], list[int]] = {}
        self._ids: list[str] = []
        self._sigs: list[np.ndarray] = []

    def __len__(self):
        return len(self._ids)

    def add(self, item_id: str, sig: np.ndarray) -> None:
        idx = len(self._ids)
        self._ids.append(item_id)
        self._sigs.append(sig)
        for b in range(BANDS):
            self._buckets.setdefault((b, sig[b * ROWS:(b + 1) * ROWS].tobytes()), []).append(idx)

    def query(self, sig: np.ndarray, min_sim: float = 0.0) -> list[tuple[str, float]]:
        """All indexed items sharing >=1 band with `sig` and est. Jaccard >= min_sim,
        best first; ties broken by insertion order (deterministic)."""
        cand: set[int] = set()
        for b in range(BANDS):
            cand.update(self._buckets.get((b, sig[b * ROWS:(b + 1) * ROWS].tobytes()), ()))
        out = []
        for i in sorted(cand):
            s = est_jaccard(sig, self._sigs[i])
            if s >= min_sim:
                out.append((self._ids[i], s, i))
        out.sort(key=lambda t: (-t[1], t[2]))
        return [(i, s) for i, s, _ in out]
