"""Print sample duplicate pairs per similarity band, from a finished run.

Dropped near-dups come from ledger.csv (reason near_duplicate:J=..., dup_of=kept id);
near-misses (kept, but similar to a kept doc) from analysis/near_dup_near_misses.tsv.
Usage: PYTHONPATH=src python3 tools/near_dup_report.py <raw_dir> <out_dir> [per_band]
"""
import collections, csv, hashlib, random, re, sys
from falcon_corpus import pipeline as P, readers

raw, out = sys.argv[1], sys.argv[2]
per = int(sys.argv[3]) if len(sys.argv) > 3 else 6
plan = readers.plan_inputs(raw)
for e in plan:
    e["fid"] = hashlib.sha1(e["path"].encode()).hexdigest()[:12]
text, lang = {}, {}
for r in P.iter_pass1(out + "/_work", plan):
    if r["status"] == "candidate":
        text[r["id"]], lang[r["id"]] = r["text"], r["lang"]
pairs = []   # (sim, a, b, kind)
for row in csv.DictReader(open(out + "/ledger.csv", encoding="utf-8")):
    m = re.match(r"near_duplicate:J=([\d.]+)", row["reason"])
    if m:
        pairs.append((float(m.group(1)), row["id"], row["dup_of"], "dropped"))
for row in csv.DictReader(open(out + "/analysis/near_dup_near_misses.tsv", encoding="utf-8"), delimiter="\t"):
    pairs.append((float(row["sim"]), row["doc_id"], row["other_id"], "kept"))
random.seed(5)
bands = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
for lg in ("hi", "hi-Latn"):
    print(f"\n######## {lg}   counts per band:", {f"{lo}-{min(hi,1.0)}": sum(1 for s, a, b, k in pairs if lo <= s < hi and lang.get(a) == lg) for lo, hi in bands})
    for lo, hi in bands:
        sel = [p for p in pairs if lo <= p[0] < hi and lang.get(p[1]) == lg]
        random.shuffle(sel)
        print(f"\n--- {lg} J in [{lo},{min(hi,1.0)})  n={len(sel)}")
        for s, a, b, k in sel[:per]:
            print(f"  {s:.2f} {k:7s} A: {text[a][:120]}\n               B: {text[b][:120]}")
