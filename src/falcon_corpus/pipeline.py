"""The pipeline.

  raw files --(pass 1, stateless, per chunk, resumable)--> work shards
  work shards --(pass 2, stateful, always rerun from scratch)--> outputs

Pass 1 per document:  parse -> normalize -> language -> quality -> PII redaction
                      -> match key + MinHash signatures.
Pass 2:  carve eval set -> contamination check against it -> exact dedup -> near dedup.

Why the eval set is carved BEFORE dedup: if dedup ran first it would silently
delete the training-side copies of every eval item and the leak check would then
report zero. Carving first means copies are found, reported and removed.

Crash safety: pass-1 chunks are written to a temp name and renamed (a chunk file
either exists completely or not at all). Pass 2 is deterministic and writes every
output to a temp directory that is moved into place only at the end, so a run
killed anywhere and restarted produces output byte-identical to an uninterrupted one.
"""
from __future__ import annotations

import csv
import dataclasses
import difflib
import gzip
import hashlib
import heapq
import io
import json
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from . import langid, minhash, normalize, pii, quality, readers, translit

STAGES = ["parse", "lang", "filter", "eval", "contamination", "dedup"]


@dataclass
class Config:
    keep_langs: tuple = ("hi", "hi-Latn")
    keep_neutral: bool = True          # no-letter docs (tables of numbers) are language-neutral: keep
    chunk_size: int = 20000            # records per pass-1 shard = unit of resume
    shard_rows: int = 20000            # rows per clean/ part file
    near_threshold: float = 0.80       # est. Jaccard on char 5-grams of the match key
    near_log_floor: float = 0.50       # near-misses at/above this are logged for review
    contam_threshold: float = 0.70     # eval leak: more sensitive than dedup (removing a doc is cheap)
    contam_char_sim: float = 0.90      # ... or this much character-level similarity (short strings, spelling variants)
    translit_threshold: float = 0.60   # cross-script skeleton Jaccard
    translit_min_len: int = 12         # skeleton must have this many chars to be compared
    eval_size: int = 200
    eval_max_fraction: float = 0.10    # never carve more than this share of the eligible docs (small inputs)
    eval_hi_share: float = 0.6
    eval_seed: int = 17
    eval_min_words: int = 5
    eval_max_chars: int = 400
    workers: int = 1                   # not part of the output identity

    def identity(self) -> dict:
        d = dataclasses.asdict(self)
        d.pop("workers")
        return d


# ----------------------------------------------------------------------------
# small io helpers
# ----------------------------------------------------------------------------
def _gz_write(path: str, lines) -> int:
    """Deterministic gzip (mtime=0, no filename), atomic via temp+rename."""
    tmp = path + ".tmp"
    n = 0
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz:
            for line in lines:
                gz.write(line.encode("utf-8") + b"\n")
                n += 1
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(tmp, path)
    return n


def _gz_read(path: str):
    with gzip.open(path, "rb") as f:
        for line in f:
            yield json.loads(line)


def _dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: str) -> str:
    return readers.file_sha256(path)


def code_hash() -> str:
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):
        if name.endswith(".py"):
            h.update(name.encode())
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _log(msg: str) -> None:
    print(f"[falcon] {msg}", file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------
# pass 1: per-document processing (pure function of the raw record + config)
# ----------------------------------------------------------------------------
_DIGITS = __import__("re").compile(r"\d+")


def digit_signature(key: str) -> str:
    """Ordered digit sequences of a match key (already ASCII-digit mapped). Two texts that
    differ in a number are different facts ("10 minutes" / "20 minutes"), so they are never
    near-duplicates of each other, however similar the rest is."""
    return hashlib.sha1("|".join(_DIGITS.findall(key)).encode()).hexdigest()[:8]


def char_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def process_doc(d: readers.RawDoc, cfg: Config) -> dict:
    base = {"id": d.id, "source": d.source, "path": d.path, "ordinal": d.ordinal}
    if d.error:
        return {**base, "status": "dropped", "stage": "parse", "reason": d.error, "lang": "", "edits": ""}

    text, flags = normalize.normalize_text(d.text)
    edits = sorted(f for f in flags if f != "changed")

    lg = langid.identify(text)
    neutral = lg.why == "no_letters"
    if lg.lang not in cfg.keep_langs and not (neutral and cfg.keep_neutral):
        return {**base, "status": "dropped", "stage": "lang", "reason": f"lang={lg.lang} ({lg.why})",
                "lang": lg.lang, "edits": ",".join(edits)}

    reason, text, qflags = quality.check(text)
    edits += qflags
    if reason:
        return {**base, "status": "dropped", "stage": "filter", "reason": reason,
                "lang": lg.lang, "edits": ",".join(edits)}

    text, counts = pii.redact(text)
    edits += [f"pii:{k}={v}" for k, v in sorted(counts.items())]

    key = normalize.match_key(text)
    rec = {**base, "status": "candidate", "lang": lg.lang, "text": text, "edits": ",".join(edits),
           "kh": hashlib.sha1(key.encode("utf-8")).hexdigest()[:24], "dg": digit_signature(key),
           "sig": minhash.sig_to_str(minhash.signature(key)), "script": translit.script_of(text)}
    sk = translit.skeleton(text)
    if len(sk) >= cfg.translit_min_len:
        rec["tsig"] = minhash.sig_to_str(minhash.signature(sk, 4))
    return rec


def _process_chunk(args) -> list[str]:
    docs, cfg_dict = args
    cfg = Config(**cfg_dict)
    return [_dumps(process_doc(readers.RawDoc(*d), cfg)) for d in docs]


def _doc_tuple(d: readers.RawDoc):
    return (d.source, d.path, d.ordinal, d.text, d.error, d.raw_hash, d.meta_lang)


def pass1(raw_dir: str, plan: list[dict], work: str, cfg: Config, crash_after: int | None) -> dict:
    stats = Counter()
    p1 = os.path.join(work, "pass1")
    os.makedirs(p1, exist_ok=True)
    pool = None
    if cfg.workers > 1:
        import multiprocessing as mp
        pool = mp.get_context("fork").Pool(cfg.workers)
    cfgd = dataclasses.asdict(cfg)
    chunks_written = 0
    try:
        for entry in plan:
            fid = entry["fid"]
            fdir = os.path.join(p1, fid)
            done_marker = os.path.join(fdir, "DONE.json")
            if os.path.exists(done_marker):
                stats["files_resumed"] += 1
                continue
            os.makedirs(fdir, exist_ok=True)
            buf: list[readers.RawDoc] = []
            idx = 0
            nrec = 0

            def flush(buf, idx):
                nonlocal chunks_written
                path = os.path.join(fdir, f"chunk-{idx:05d}.jsonl.gz")
                if os.path.exists(path):
                    stats["chunks_resumed"] += 1
                    return
                tuples = [_doc_tuple(d) for d in buf]
                if pool:
                    parts = pool.map(_process_chunk, [(tuples[i:i + 2000], cfgd) for i in range(0, len(tuples), 2000)])
                    lines = [ln for part in parts for ln in part]
                else:
                    lines = _process_chunk((tuples, cfgd))
                _gz_write(path, lines)
                chunks_written += 1
                stats["chunks_written"] += 1
                if crash_after is not None and chunks_written >= crash_after:
                    _log(f"fault injection: simulated crash after {chunks_written} chunks")
                    os._exit(137)

            for d in readers.iter_file(raw_dir, entry):
                buf.append(d)
                nrec += 1
                if len(buf) >= cfg.chunk_size:
                    flush(buf, idx); idx += 1; buf = []
            if buf or idx == 0:
                flush(buf, idx); idx += 1
            with open(done_marker + ".tmp", "w") as f:
                json.dump({"chunks": idx, "records": nrec}, f)
            os.replace(done_marker + ".tmp", done_marker)
            stats["records_read"] += nrec
            _log(f"pass1 {entry['path']}: {nrec} records, {idx} chunk(s)")
    finally:
        if pool:
            pool.close(); pool.join()
    return dict(stats)


def iter_pass1(work: str, plan: list[dict]):
    for entry in plan:
        fdir = os.path.join(work, "pass1", entry["fid"])
        n = json.load(open(os.path.join(fdir, "DONE.json")))["chunks"]
        for i in range(n):
            yield from _gz_read(os.path.join(fdir, f"chunk-{i:05d}.jsonl.gz"))


# ----------------------------------------------------------------------------
# pass 2: eval carve, contamination, dedup
# ----------------------------------------------------------------------------
def _reason_bucket(stage: str, reason: str) -> str:
    """Collapse parameterised reasons ('lang=en (...)', 'near_duplicate:J=0.91') for counting."""
    if stage == "lang":
        return reason.split(" ")[0]
    if stage in ("dedup", "contamination", "eval"):
        return reason.split(":")[0]
    return reason


_METHOD_RANK = {"exact": 0, "normalized": 1, "near": 2, "translit": 3}



def _eligible_for_eval(r: dict, cfg: Config) -> bool:
    t = r["text"]
    return (r["lang"] in ("hi", "hi-Latn") and len(t) <= cfg.eval_max_chars
            and len(t.split()) >= cfg.eval_min_words and "<PHONE>" not in t and "<EMAIL>" not in t
            and "<AADHAAR>" not in t and "<PAN>" not in t)


def select_eval(work, plan, cfg: Config) -> tuple[list[dict], dict]:
    """Stratified, hash-ranked, streaming (bounded heaps), one item per distinct match key.

    Each language keeps the `eval_size` smallest hash ranks seen; afterwards the quota
    (60/40 split, capped at `eval_max_fraction` of the eligible docs so a small input is
    not eaten by its own eval set) takes the best-ranked items of each heap.
    """
    langs = ("hi", "hi-Latn")
    heaps = {k: [] for k in langs}             # max-heap via negated rank
    in_heap = {k: {} for k in langs}
    eligible = Counter()
    order = 0
    for r in iter_pass1(work, plan):
        order += 1
        if r["status"] != "candidate" or not _eligible_for_eval(r, cfg):
            continue
        lang = r["lang"]
        eligible[lang] += 1
        if r["kh"] in in_heap[lang]:
            continue                            # later copies of an eval candidate stay in training -> detected as leaks
        rank = int(hashlib.sha1(f"{cfg.eval_seed}|{r['kh']}".encode()).hexdigest()[:15], 16)
        h = heaps[lang]
        entry = (-rank, -order, r)             # `order` is unique, so records are never compared
        if len(h) < cfg.eval_size:
            heapq.heappush(h, entry); in_heap[lang][r["kh"]] = True
        elif rank < -h[0][0]:                  # root = largest rank currently selected
            evicted = heapq.heapreplace(h, entry)
            in_heap[lang].pop(evicted[2]["kh"], None); in_heap[lang][r["kh"]] = True
    want_hi = round(cfg.eval_size * cfg.eval_hi_share)
    quota = {"hi": want_hi, "hi-Latn": cfg.eval_size - want_hi}
    quota = {k: min(q, int(eligible[k] * cfg.eval_max_fraction)) for k, q in quota.items()}
    chosen = []
    for lang in langs:
        items = sorted(heaps[lang], key=lambda t: (-t[0], -t[1]))   # ascending rank
        chosen += [t[2] for t in items[:quota[lang]]]
    for i, r in enumerate(chosen):
        r["eval_id"] = f"eval-{i + 1:04d}"
    return chosen, {"eligible": dict(eligible), "quota": quota}


class _Writer:
    """Rotating clean/ part writer (rows buffered per shard)."""

    def __init__(self, dirpath, rows):
        self.dir, self.rows, self.buf, self.n, self.part = dirpath, rows, [], 0, 0
        os.makedirs(dirpath, exist_ok=True)
        self.files = []

    def add(self, row: dict):
        self.buf.append(_dumps(row)); self.n += 1
        if len(self.buf) >= self.rows:
            self.flush()

    def flush(self):
        if self.buf:
            p = os.path.join(self.dir, f"part-{self.part:05d}.jsonl.gz")
            _gz_write(p, self.buf)
            self.files.append(p); self.part += 1; self.buf = []


def pass2(work: str, plan: list[dict], tmp_out: str, cfg: Config, crash_in_pass2: bool) -> dict:
    ev, ev_info = select_eval(work, plan, cfg)
    ev_by_id = {r["id"]: r for r in ev}
    _log(f"eval carve: {len(ev)} items ({dict(Counter(r['lang'] for r in ev))}); eligible docs {ev_info['eligible']}")

    # indices over the eval set
    ev_exact = {r["text"]: r for r in ev}
    ev_key = {r["kh"]: r for r in ev}
    ev_lsh, ev_tlsh = minhash.LSHIndex(), minhash.LSHIndex()
    for r in ev:
        ev_lsh.add(r["eval_id"], minhash.sig_from_str(r["sig"]))
        if "tsig" in r:
            ev_tlsh.add(r["eval_id"], minhash.sig_from_str(r["tsig"]))
    ev_by_eid = {r["eval_id"]: r for r in ev}

    seen_kh: dict[str, str] = {}
    kept_dg: dict[str, str] = {}
    lsh = minhash.LSHIndex()
    contam: dict[str, dict[str, list]] = {}
    contam_near_miss, near_miss = [], []
    clean = _Writer(os.path.join(tmp_out, "clean"), cfg.shard_rows)
    led_path = os.path.join(tmp_out, "ledger.csv")
    outcome = Counter()                        # (lang, 'kept' | 'dropped:<stage>') -> n
    reasons = Counter()
    edit_counts = Counter()
    per_source = Counter()
    ledger_f = open(led_path, "w", encoding="utf-8", newline="")
    led = csv.writer(ledger_f, lineterminator="\n")
    led.writerow(["id", "status", "stage", "reason", "dup_of", "source", "path", "ordinal", "lang", "edits"])

    def ledger(r, status, stage, reason="", dup_of=""):
        led.writerow([r["id"], status, stage, reason, dup_of, r["source"], r["path"], r["ordinal"], r.get("lang", ""), r.get("edits", "")])
        lang = r.get("lang", "") or "unparsed"
        outcome[(lang, "kept" if status == "kept" else f"dropped:{stage}")] += 1
        per_source[(r["source"], status)] += 1
        if status == "dropped":
            reasons[(stage, _reason_bucket(stage, reason))] += 1
        for e in (r.get("edits") or "").split(","):
            if e:
                edit_counts[e.split("=")[0] if e.startswith("pii:") else e.split(":")[0]] += 1

    n_seen = 0
    for r in iter_pass1(work, plan):
        n_seen += 1
        if crash_in_pass2 and n_seen == 5000:
            _log("fault injection: simulated crash inside pass 2")
            os._exit(137)
        if r["status"] == "dropped":
            ledger(r, "dropped", r["stage"], r["reason"])
            continue
        if r["id"] in ev_by_id:
            ledger(r, "dropped", "eval", f"held_out_for_eval:{ev_by_id[r['id']]['eval_id']}")
            continue

        # ---- contamination vs eval ------------------------------------------
        hits: dict[str, tuple[str, float]] = {}       # eval_id -> (strongest method, sim)

        def note(eid, method, sim):
            if eid not in hits or _METHOD_RANK[method] < _METHOD_RANK[hits[eid][0]]:
                hits[eid] = (method, sim)

        if r["text"] in ev_exact:
            note(ev_exact[r["text"]]["eval_id"], "exact", 1.0)
        if r["kh"] in ev_key:
            e = ev_key[r["kh"]]
            note(e["eval_id"], "exact" if e["text"] == r["text"] else "normalized", 1.0)
        sig = minhash.sig_from_str(r["sig"])
        ev_cands = ev_lsh.query(sig, cfg.near_log_floor)
        rkey = normalize.match_key(r["text"]) if ev_cands else ""
        for eid, sm in ev_cands:
            e = ev_by_eid[eid]
            if e["dg"] != r["dg"]:
                contam_near_miss.append((eid, r["id"], "near", sm, "digits_differ"))
                continue
            cs = char_similarity(normalize.match_key(e["text"]), rkey)
            if sm >= cfg.contam_threshold or cs >= cfg.contam_char_sim:
                note(eid, "near", max(sm, cs) if sm < cfg.contam_threshold else sm)
            else:
                contam_near_miss.append((eid, r["id"], "near", sm, "below_threshold"))
        if "tsig" in r and len(ev_tlsh):
            tsig = minhash.sig_from_str(r["tsig"])
            for eid, s in ev_tlsh.query(tsig, cfg.near_log_floor):
                if ev_by_eid[eid]["script"] == r["script"]:
                    continue                           # same script: other methods already cover it
                if s >= cfg.translit_threshold:
                    note(eid, "translit", s)
                else:
                    contam_near_miss.append((eid, r["id"], "translit", s, "below_threshold"))
        if hits:
            eid, (method, sim) = min(hits.items(), key=lambda kv: (_METHOD_RANK[kv[1][0]], -kv[1][1], kv[0]))
            for e2, (m2, s2) in hits.items():
                contam.setdefault(e2, {}).setdefault(m2, []).append((r["id"], round(s2, 3)))
            ledger(r, "dropped", "contamination", f"{method}:{eid}:{sim:.2f}", ev_by_eid[eid]["id"])
            continue

        # ---- dedup -----------------------------------------------------------
        if r["kh"] in seen_kh:
            ledger(r, "dropped", "dedup", "exact_duplicate", seen_kh[r["kh"]])
            continue
        cands = lsh.query(sig, cfg.near_log_floor)
        same_num = [(o, sm) for o, sm in cands if kept_dg[o] == r["dg"]]
        if same_num and same_num[0][1] >= cfg.near_threshold:
            ledger(r, "dropped", "dedup", f"near_duplicate:J={same_num[0][1]:.2f}", same_num[0][0])
            continue
        for other, sm in cands[:3]:
            why = "digits_differ" if kept_dg[other] != r["dg"] else "below_threshold"
            near_miss.append((r["id"], other, sm, why))
        seen_kh[r["kh"]] = r["id"]
        kept_dg[r["id"]] = r["dg"]
        lsh.add(r["id"], sig)
        clean.add({"id": r["id"], "text": r["text"], "lang": r["lang"], "source": r["source"]})
        ledger(r, "kept", "final")
    clean.flush()
    ledger_f.close()

    # eval + contamination outputs
    with open(os.path.join(tmp_out, "eval.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for r in ev:
            f.write(_dumps({"eval_id": r["eval_id"], "id": r["id"], "text": r["text"], "lang": r["lang"], "source": r["source"]}) + "\n")
    with open(os.path.join(tmp_out, "contamination.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for eid in sorted(contam):
            by_method = contam[eid]
            strongest = min(by_method, key=lambda m: _METHOD_RANK[m])
            f.write(_dumps({"eval_id": eid, "train_ids": sorted({i for v in by_method.values() for i, _ in v}),
                            "method": strongest,
                            "methods": {m: [{"id": i, "sim": s} for i, s in v] for m, v in sorted(by_method.items())}}) + "\n")

    ana = os.path.join(tmp_out, "analysis")
    os.makedirs(ana, exist_ok=True)
    texts = {}
    want = {a for a, b, s, w in near_miss} | {b for a, b, s, w in near_miss} | {x[1] for x in contam_near_miss}
    if want:
        for r in iter_pass1(work, plan):
            if r["id"] in want:
                texts[r["id"]] = r["text"]
    with open(os.path.join(ana, "near_dup_near_misses.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("sim\twhy_kept\tdoc_id\tother_id\tdoc_text\tother_text\n")
        for a, b, sm, why in sorted(near_miss, key=lambda t: (-t[2], t[0]))[:20000]:
            f.write(f"{sm:.3f}\t{why}\t{a}\t{b}\t{texts.get(a, '').replace(chr(9), ' ')}\t{texts.get(b, '').replace(chr(9), ' ')}\n")
    with open(os.path.join(ana, "contamination_near_misses.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("method\tsim\twhy_rejected\teval_id\tdoc_id\teval_text\tdoc_text\n")
        for eid, did, m, sm, why in sorted(contam_near_miss, key=lambda t: (-t[3], t[0], t[1]))[:20000]:
            f.write(f"{m}\t{sm:.3f}\t{why}\t{eid}\t{did}\t{ev_by_eid[eid]['text'].replace(chr(9), ' ')}\t{texts.get(did, '').replace(chr(9), ' ')}\n")

    return {"outcome": outcome, "reasons": reasons, "edits": edit_counts, "clean_rows": clean.n, "clean_files": clean.files,
            "eval_items": len(ev), "eval_info": ev_info, "contaminated_eval_items": len(contam), "per_source": per_source,
            "records_total": n_seen}


# ----------------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------------
def _funnel(outcome: Counter) -> list[dict]:
    langs = sorted({l for l, _ in outcome})
    total = {l: sum(n for (ll, _), n in outcome.items() if ll == l) for l in langs}
    rows = [{"stage": "input", **{l: total[l] for l in langs}, "all": sum(total.values())}]
    remaining = dict(total)
    for st in STAGES:
        for l in langs:
            remaining[l] -= outcome.get((l, f"dropped:{st}"), 0)
        rows.append({"stage": f"after_{st}", **{l: remaining[l] for l in langs}, "all": sum(remaining.values())})
    return rows


def run(raw_dir: str, out_dir: str, cfg: Config) -> int:
    t0 = time.time()
    raw_dir, out_dir = os.path.abspath(raw_dir), os.path.abspath(out_dir)
    if not os.path.isdir(raw_dir):
        _log(f"raw_dir not found: {raw_dir}")
        return 2
    os.makedirs(out_dir, exist_ok=True)

    plan = readers.plan_inputs(raw_dir)
    for e in plan:
        e["fid"] = hashlib.sha1(e["path"].encode()).hexdigest()[:12]
    all_files = readers._all_files(raw_dir)
    inputs = [{"path": p, "bytes": os.path.getsize(os.path.join(raw_dir, p)), "sha256": _sha256(os.path.join(raw_dir, p))} for p in all_files]
    chash = code_hash()
    fingerprint = hashlib.sha256(_dumps({"inputs": inputs, "plan": plan, "config": cfg.identity(), "code": chash}).encode()).hexdigest()

    work = os.path.join(out_dir, "_work")
    fp_path = os.path.join(work, "fingerprint")
    if os.path.exists(fp_path) and open(fp_path).read() != fingerprint:
        _log("inputs/config/code changed since the last run: discarding stale work shards")
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)
    with open(fp_path, "w") as f:
        f.write(fingerprint)
    _log(f"{len(plan)} input file(s), code {chash[:10]}, fingerprint {fingerprint[:10]}")

    crash_after = int(os.environ["FALCON_CRASH_AFTER_CHUNKS"]) if os.environ.get("FALCON_CRASH_AFTER_CHUNKS") else None
    crash_p2 = bool(os.environ.get("FALCON_CRASH_IN_PASS2"))
    p1 = pass1(raw_dir, plan, work, cfg, crash_after)

    tmp_out = os.path.join(out_dir, "_tmp_final")
    shutil.rmtree(tmp_out, ignore_errors=True)
    os.makedirs(tmp_out)
    res = pass2(work, plan, tmp_out, cfg, crash_p2)

    sources = readers.load_sources(raw_dir)
    outputs = []
    for rel in ["ledger.csv", "eval.jsonl", "contamination.jsonl"] + [os.path.join("clean", os.path.basename(p)) for p in res["clean_files"]]:
        p = os.path.join(tmp_out, rel)
        outputs.append({"path": rel, "bytes": os.path.getsize(p), "sha256": _sha256(p)})
    manifest = {
        "sources": sources,
        "inputs": inputs,
        "config": cfg.identity(),
        "tunables": {
            "langid": {k: getattr(langid, k) for k in ("DEV_SHARE_HI", "DEV_SHARE_MIXED", "HI_LATN_MIN_SHARE", "OTHER_SCRIPT_MAX")},
            "quality": {k: getattr(quality, k) for k in dir(quality) if k.isupper() and isinstance(getattr(quality, k), (int, float))},
            "minhash": {"num_perm": minhash.NUM_PERM, "ngram": minhash.NGRAM, "bands": minhash.BANDS, "rows": minhash.ROWS},
        },
        "code_sha256": chash,
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__},
        "counts": {
            "records_total": res["records_total"],
            "kept": res["clean_rows"],
            "eval_items": res["eval_items"],
            "eval_info": res["eval_info"],
            "contaminated_eval_items": res["contaminated_eval_items"],
            "funnel": _funnel(res["outcome"]),
            "dropped_by_stage_and_reason": {f"{s}|{r}": n for (s, r), n in sorted(res["reasons"].items())},
            "edits": dict(sorted(res["edits"].items())),
            "per_source": {f"{s}|{st}": n for (s, st), n in sorted(res["per_source"].items())},
            "skipped_malformed": {r: n for (s, r), n in sorted(res["reasons"].items()) if s == "parse"},
        },
        "outputs": outputs,
    }
    with open(os.path.join(tmp_out, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    # ---- publish: replace old outputs with the new set -----------------------
    for name in ("clean", "analysis"):
        shutil.rmtree(os.path.join(out_dir, name), ignore_errors=True)
    for name in ("ledger.csv", "eval.jsonl", "contamination.jsonl", "manifest.json"):
        if os.path.exists(os.path.join(out_dir, name)):
            os.remove(os.path.join(out_dir, name))
    for name in sorted(os.listdir(tmp_out)):
        os.replace(os.path.join(tmp_out, name), os.path.join(out_dir, name))
    os.rmdir(tmp_out)

    c = manifest["counts"]
    _log(f"done in {time.time() - t0:.1f}s: {c['records_total']} in -> {c['kept']} kept, eval {c['eval_items']}, "
         f"contaminated eval items {c['contaminated_eval_items']}")
    return 0
