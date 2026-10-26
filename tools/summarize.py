"""Numbers for DECISIONS.md, computed from a finished run. Usage: PYTHONPATH=src python3 tools/summarize.py <raw_dir> <out_dir>"""
import collections, csv, gzip, glob, json, os, statistics, sys
from falcon_corpus import readers

raw, out = sys.argv[1], sys.argv[2]
m = json.load(open(f"{out}/manifest.json"))
led = list(csv.DictReader(open(f"{out}/ledger.csv", encoding="utf-8")))
clean = [json.loads(l) for f in sorted(glob.glob(f"{out}/clean/*.gz")) for l in gzip.open(f, "rt", encoding="utf-8")]

print("== per source")
srcs = {s["name"]: s for s in m["sources"]}
raw_bytes = {n: sum(f["bytes"] for f in s["files"] if f["path"].endswith((".conllu", ".tsv"))) for n, s in srcs.items()}
text_bytes = collections.Counter(); text_n = collections.Counter(); kept_n = collections.Counter(); kept_bytes = collections.Counter()
for e in readers.plan_inputs(raw):
    for d in readers.iter_file(raw, e):
        if d.text is not None:
            text_n[d.source] += 1; text_bytes[d.source] += len(d.text.encode())
for c in clean:
    kept_n[c["source"]] += 1; kept_bytes[c["source"]] += len(c["text"].encode())
for n, s in srcs.items():
    print(f"{n:22s} {s['license']:14s} {s['download_date']}  data-file bytes={raw_bytes[n]:>10,}  docs parsed={text_n[n]:>6,}  raw text bytes={text_bytes[n]:>9,}  kept={kept_n[n]:>6,}  kept text bytes={kept_bytes[n]:>9,}")
print("\n== kept by language:", dict(collections.Counter(c["lang"] for c in clean)))
for lg in ("hi", "hi-Latn"):
    L = [len(c["text"]) for c in clean if c["lang"] == lg]
    print(f"   {lg}: n={len(L)} median chars={statistics.median(L)} mean={statistics.mean(L):.1f} total chars={sum(L):,}")
print("\n== funnel");  [print("  ", r) for r in m["counts"]["funnel"]]
print("\n== dropped by stage|reason", m["counts"]["dropped_by_stage_and_reason"])
by = collections.Counter((r["lang"], r["stage"], r["reason"].split(":")[0]) for r in led if r["status"] == "dropped")
print("\n== dropped by (lang, stage, reason)"); [print("  ", k, v) for k, v in sorted(by.items())]
x = collections.Counter((r["path"].split("/")[-1], r["stage"], r["reason"].split(":")[0]) for r in led if r["stage"] in ("dedup",))
print("\n== dedup by file:"); [print("  ", k, v) for k, v in sorted(x.items())]
print("\n== edits", m["counts"]["edits"])
print("\n== contamination:", m["counts"]["eval_items"], "eval items,", m["counts"]["contaminated_eval_items"], "affected;", m["counts"]["eval_info"])
cm = collections.Counter(json.loads(l)["method"] for l in open(f"{out}/contamination.jsonl", encoding="utf-8"))
print("   strongest method per affected eval item:", dict(cm))
print("   training docs removed:", collections.Counter(r["reason"].split(":")[0] for r in led if r["stage"] == "contamination"))
