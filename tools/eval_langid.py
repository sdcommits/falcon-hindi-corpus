"""Measure language ID. Dev = Hinglish-TOP train (used for tuning); report = test.

en_query is English and cs_query is Hinglish in Hinglish-TOP, which gives us
labelled data that the word lists were not built from. UD files should be `hi`.
Usage: PYTHONPATH=src python3 tools/eval_langid.py data/raw
"""
import collections, csv, glob, os, sys
from falcon_corpus.langid import identify
from falcon_corpus.normalize import normalize_text

raw = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
csv.field_size_limit(10**9)
show = int(os.environ.get("SHOW", "0"))
for split in ("train", "validation", "test"):
    f = f"{raw}/hinglish-top/Dataset/Human Annotated Data/{split}.tsv"
    en, cs, bad_en, bad_cs = collections.Counter(), collections.Counter(), [], []
    for r in csv.DictReader(open(f, encoding="utf-8", newline=""), delimiter="\t", quoting=csv.QUOTE_NONE):
        e, c = identify(normalize_text(r["en_query"])[0]), identify(normalize_text(r["cs_query"])[0])
        en[e.lang] += 1; cs[c.lang] += 1
        if e.lang != "en": bad_en.append((r["en_query"], e.lang))
        if c.lang != "hi-Latn": bad_cs.append((r["cs_query"], c.lang))
    n = sum(en.values())
    print(f"{split:10s} n={n:5d}  en_query->en {en['en']/n:.4f} {dict(en)}   cs_query->hi-Latn {cs['hi-Latn']/n:.4f} {dict(cs)}")
    if split == "train" and show:
        print(" EN misses:", bad_en[:show]); print(" CS misses:", bad_cs[:show])
for f in sorted(glob.glob(f"{raw}/ud-*/*.conllu")):
    c = collections.Counter(identify(normalize_text(l[9:].strip())[0]).lang
                            for l in open(f, encoding="utf-8") if l.startswith("# text = "))
    print(f"{os.path.basename(f):28s} {dict(c)}")
