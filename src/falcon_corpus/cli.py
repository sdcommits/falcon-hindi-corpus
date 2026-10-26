"""Command line: python -m falcon_corpus <raw_dir> <out_dir> [options]  (run.sh wraps this)."""
from __future__ import annotations

import argparse
import dataclasses
import sys

from .pipeline import Config, run


def main(argv=None) -> int:
    d = Config()
    ap = argparse.ArgumentParser(prog="run.sh", description="Clean a raw Hindi/Hinglish corpus. Offline; deterministic; resumable.")
    ap.add_argument("raw_dir", help="directory of raw files (optionally with sources.json from download.sh)")
    ap.add_argument("out_dir", help="output directory (clean/, ledger.csv, eval.jsonl, contamination.jsonl, manifest.json)")
    ap.add_argument("--near-threshold", type=float, default=d.near_threshold, help="near-duplicate est. Jaccard (default %(default)s)")
    ap.add_argument("--contam-threshold", type=float, default=d.contam_threshold)
    ap.add_argument("--translit-threshold", type=float, default=d.translit_threshold)
    ap.add_argument("--eval-size", type=int, default=d.eval_size)
    ap.add_argument("--eval-seed", type=int, default=d.eval_seed)
    ap.add_argument("--keep-langs", default=",".join(d.keep_langs), help="comma list from hi,hi-Latn,en,other")
    ap.add_argument("--drop-neutral", action="store_true", help="also drop language-neutral docs (no letters, e.g. tables of numbers)")
    ap.add_argument("--chunk-size", type=int, default=d.chunk_size)
    ap.add_argument("--workers", type=int, default=1, help="processes for pass 1 (does not change output)")
    a = ap.parse_args(argv)
    cfg = dataclasses.replace(
        d, near_threshold=a.near_threshold, contam_threshold=a.contam_threshold, translit_threshold=a.translit_threshold,
        eval_size=a.eval_size, eval_seed=a.eval_seed, keep_langs=tuple(x for x in a.keep_langs.split(",") if x),
        keep_neutral=not a.drop_neutral, chunk_size=a.chunk_size, workers=a.workers)
    return run(a.raw_dir, a.out_dir, cfg)


if __name__ == "__main__":
    sys.exit(main())
