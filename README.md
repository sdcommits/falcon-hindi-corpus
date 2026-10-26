# falcon-hindi-corpus
Hindi / Hinglish cleaning pipeline. See `DECISIONS.md` for numbers and rationale.

    ./download.sh                       # pinned clones -> data/raw (needs GitHub)
    ./run.sh data/raw data/out          # offline; flags: --near-threshold --eval-size --workers ...
    PYTHONPATH=src:. python3 -m unittest discover -s tests -t .

Outputs in `<out>`: `clean/*.jsonl.gz`, `ledger.csv`, `eval.jsonl`, `contamination.jsonl`, `manifest.json`, `analysis/`.
Committed: `eval/eval.jsonl` (200 items), `samples/clean_sample_1000.jsonl` (seeded random 1,000 rows), `analysis/` (near-miss and contamination logs). `data/` is gitignored; `download.sh` recreates it.

eval/eval.jsonl: 200 held-out Hindi (120) and Hinglish (80) sentences. They measure how well a model predicts natural text (perplexity or completion), not downstream skills.
