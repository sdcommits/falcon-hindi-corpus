# DECISIONS

Run on 2026-10-26, defaults, `./run.sh data/raw data/out` (~19 s, 1 worker). All numbers below come from `data/out/manifest.json`, `ledger.csv` and `tools/summarize.py`.

## 1. Sources
Only GitHub and PyPI were reachable from my sandbox (Hugging Face, Wikimedia, Kaggle, archive.org, zenodo: proxy 403). I did not route around that, so there is **no web-crawl source**; all three are small, clean, sentence-level corpora. Each is pinned by git SHA in `data/raw/sources.json` (name, URL, commit, license, download date, per-file sha256); raw files are verbatim copies.

| Source | Script | License | Commit | Docs | Data bytes | Text bytes |
|---|---|---|---|---|---|---|
| UD Hindi-HDTB (news) | Devanagari | CC BY-NC-SA 4.0 | 71630e5d38 | 16,649 | 60.5 MB (conllu markup) | 4.3 MB |
| UD Hindi-PUD (translated Wikipedia/news) | Devanagari | CC BY-SA 3.0 | a68a48e094 | 1,000 | 3.0 MB | 0.29 MB |
| Hinglish-TOP, human split (voice-assistant queries) | Latin | Apache-2.0 | fdd3998a65 | 10,896 | 3.2 MB (tsv) | 0.48 MB |
Total 28,545 records. **Caveats:** HDTB is NC, so the corpus is not commercially usable as is. Hinglish is a single domain (alarms/timers/weather) in one voice; a "doc" is a sentence, median 42 chars (Hinglish) / 92 (Hindi). I'm meeting the 10k/1k counts, not the spirit of "web-scale diversity".

## 2. Funnel (docs)
| Stage | hi | hi-Latn | en/other | all |
|---|---|---|---|---|
| input / parse (0 malformed) | 17,649 | 10,745 | 151 | 28,545 |
| after language | 17,649 | 10,745 | 0 | 28,394 |
| after quality filter | 17,649 | 10,745 | 0 | 28,394 |
| after eval carve-out (200) | 17,529 | 10,665 | 0 | 28,194 |
| after contamination (30) | 17,529 | 10,635 | 0 | 28,164 |
| after dedup (1,097 exact + 236 near) | **17,478** | **9,353** | 0 | **26,831** |
Language is my own LID, not source labels. The 151 "en" are all TOP rows: mostly short genuine Hinglish with no evidence word ("Dallas me events", "timer me ten minutes jod do") that I drop for precision. That's ~1.4% of TOP lost — a deliberate recall cost. Quality filters drop 0 real docs here (the data is already clean); they are exercised by synthetic junk in `tests/`. PII: 0 hits in real data; the one 12-digit number (a passport ID in HDTB, Verhoeff-invalid, no Aadhaar context) survives by design. 120 docs had invisible chars removed. 98% of dedup losses are Hinglish, because TOP's train/validation/test splits overlap heavily.

## 3. Near-duplicates
MinHash, 128 perms, char 5-grams of an aggressive match key (NFKC, casefold, no punctuation/space, nukta/chandrabindu folded), 32 bands × 4 rows, candidate verified by estimated Jaccard. **Threshold 0.80**, chosen by reading sampled pairs per band (`tools/near_dup_report.py`): Hindi ≥0.8 are true near-copies, 0.6–0.7 are different stories or paraphrases; Hinglish ≥0.8 are spelling variants, 0.7–0.8 mostly different queries. **Digit guard:** pairs whose digit sequences differ are never near-dups (without it 58 of 282 drops were "5 minutes" vs "15 minutes"; 2,427 such pairs logged in `analysis/near_dup_near_misses.tsv`). Exact dups are by match-key hash; the first occurrence in input order is kept.
Three pairs at the threshold:
- J=0.80 **dropped**: "kitna time bacha hai" ≈ kept "Kitna time bacha he?" — same query, spelling variant. Right call for training value.
- J=0.80 **dropped**: "timer ko reset karo" ≈ kept "timer ko reset kare" — imperative-form variant. Debatable: it's a real orthographic/morphological variant, and I lose it. Kept policy: dedup anyway, because TOP has hundreds of these.
- J=0.797 **kept**: "baarish kab rukegi?" vs "Aaj baarish kab rukegi" — adds one word ("today"). Kept; arguably a dup. A threshold of 0.75 would have merged many genuinely different queries ("alarm set kare" family), so I accepted the miss.

## 4. Wrongly removed documents
My first run falsely dropped 13 docs, all by the boilerplate rule (substring match). Three, now fixed (commit 2b99b78; the rule needs boilerplate phrases to *cover* the line, not just appear):
1. "mere cookie timer par kitna time elapse hua hai" — a cooking-timer query hit `cookie`.
2. HDTB "वह टेलीविजन पर धूम्रपान के खिलाफ तीन विज्ञापन कर चुके हैं." — a real sentence hit `विज्ञापन` (advertisement).
3. HDTB "उत्तरी ग्रिड गुरुवार को फेल होते - होते बचा।" — mislabeled Marathi because my Marathi lexicon contained common Hindi words; trimmed.
Current residual wrong removals: the 151 short Hinglish "en" rows above (e.g. "please snooze karen"), and near-dup collapses like pair 2 in §3. I did not find any real doc dropped by a quality filter after the fix, but with 0 drops on clean sentence data that is weak evidence; the filters are untested on real web junk.

## 5. Contamination
Eval set: 200 items (120 hi / 80 hi-Latn), drawn from the corpus itself by hash rank, stratified, twin-free; it measures "does a model reproduce held-out Hindi/Hinglish sentences" (perplexity/completion), not downstream skill. Eval is carved **before** dedup so dedup can't hide a leak. Checks, strongest first: exact (raw hash), normalized (match key equal), near (J≥0.70 or char-similarity ≥0.90, equal digits), and translit (Devanagari↔Latin consonant-skeleton MinHash, ≥0.60, skeleton ≥12 chars). **9 of 200 eval items had leaks (5 exact, 4 near); 30 training docs removed (7 exact, 23 near)**; details in `contamination.jsonl`. Example: eval-0124 "Kya muje umbrella lane ki zarurat he?" has an exact twin in train. Dedup keeps spelling variants but contamination removes them: a leak costs more than a lost training row.
Rejected candidate: eval "10 minutes ke liye timer set kardo" vs train "20 minutes ke liye timer set kare" — J=0.805 but digits differ, so not a leak (136 rejected total: 101 digits_differ, 35 below threshold). Translit found 0 hits on this data (no Devanagari/Latin pairs share a source); it is unit-tested, but brittle on casual spellings (gaon/गाँव, chai/चाय score ~0.65–0.72 on sentences, ISO 1.0), so recall is limited.

## 6. Reproducibility
Inputs pinned by commit + sha256 (`sources.json`, `manifest.json`); `manifest.json` records config, thresholds, code sha256 and versions, with no timestamps. Gzip is written with mtime=0, MinHash uses a frozen RandomState seed, order is input order. Pass 1 writes atomic chunk shards + DONE markers; a fingerprint of inputs+config+code invalidates stale work; pass 2 always reruns from scratch and publishes via atomic rename. Verified on real data: fresh, killed after 3 pass-1 chunks then restarted, killed mid-pass-2 then restarted, and `--workers 2` all give the identical combined sha256 (ledger 28,545 rows, clean 26,831 rows, no duplicate ids). `FALCON_CRASH_AFTER_CHUNKS` / `FALCON_CRASH_IN_PASS2` inject faults. 43 tests: `PYTHONPATH=src:. python3 -m unittest discover -s tests -t .`

## 7. Scale: 2 TB on object storage
The first thing to break is pass 2's in-memory state: exact-hash set plus the LSH index of 128-slot signatures (~0.5 KB/doc). Fine for 28k docs, but ~500M web docs would need hundreds of GB of RAM, and pass 2 is serial. After that: fingerprinting by fully hashing every input (cheap here, an hour of I/O at 2 TB), local-disk work dir instead of object-store, and one-file-at-a-time readers. Fix: shard by band key (map-reduce over LSH buckets, union-find on candidate pairs), disk-backed exact-hash partitions, and move pass 1 (already stateless per chunk) to a job queue. Eval carve-out and contamination stay small (index only 200 items).

## 8. Wrap-up
Not done, in priority order: (1) a real noisy web source (blocked by network), so quality filters and PII are tested only on fixtures; (2) eval-internal near-dups only partly handled; (3) translit contamination recall on casual spellings; (4) LID long tail on very short Hinglish; (5) legacy font encodings (Kruti Dev) not detected; (6) parquet reader untested; (7) pass 2 not parallel. Time: built with AI assistance. The agent took about 30 minutes of wall-clock; I spent about 2 hours running, reviewing and checking it (including a Windows test run). Not a human-equivalent estimate.
