#!/usr/bin/env bash
# Fetch the raw corpus. Usage: ./download.sh [raw_dir]   (default: data/raw)
#
# This is the ONLY script that touches the network; run.sh works offline.
# Every source is pinned to a git commit, so re-running yields byte-identical
# files. Files are copied unmodified; nothing is cleaned here.
#
# Writes <raw_dir>/sources.json: name, URL, license, commit, download date and
# the sha256 of every file we copied. run.sh reads it to attach `source`.
set -euo pipefail

RAW="${1:-data/raw}"
TODAY="$(date -u +%F)"
mkdir -p "$RAW"
RAW="$(cd "$RAW" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# fetch_repo <dest-dir-name> <github-url> <commit-sha> <file>...
# Shallow-fetches exactly one commit, then copies the listed files verbatim.
fetch_repo() {
  local dest="$1" url="$2" sha="$3"; shift 3
  local work="$TMP/$dest"
  mkdir -p "$work" "$RAW/$dest"
  git -C "$work" init -q
  git -C "$work" remote add origin "$url.git"
  GIT_TERMINAL_PROMPT=0 git -C "$work" fetch -q --depth 1 origin "$sha"
  git -C "$work" checkout -q FETCH_HEAD
  for f in "$@"; do
    mkdir -p "$RAW/$dest/$(dirname "$f")"
    cp -p "$work/$f" "$RAW/$dest/$f"
  done
}

# --- Hindi, Devanagari -------------------------------------------------------
# Hindi-Hindi Dependency Treebank (news text). CC BY-NC-SA 4.0: non-commercial.
fetch_repo ud-hindi-hdtb https://github.com/UniversalDependencies/UD_Hindi-HDTB \
  71630e5d38d65fa1af8c295fc70f5f05c662498f \
  hi_hdtb-ud-train.conllu hi_hdtb-ud-dev.conllu hi_hdtb-ud-test.conllu \
  README.md LICENSE.txt

# Parallel Universal Dependencies, Hindi (Wikipedia-derived). CC BY-SA 3.0.
fetch_repo ud-hindi-pud https://github.com/UniversalDependencies/UD_Hindi-PUD \
  a68a48e09451b675e2853106474dda4dd7515510 \
  hi_pud-ud-test.conllu README.md LICENSE.txt

# --- Hinglish, Latin script --------------------------------------------------
# Hinglish-TOP, human-annotated split only (cs_query column = code-mixed
# utterance). The 170k-row synthetic split is deliberately NOT fetched: it is
# templated machine output and would swamp the Hindi share (see DECISIONS.md).
fetch_repo hinglish-top https://github.com/google-research-datasets/Hinglish-TOP-Dataset \
  fdd3998a6573130659bfa1ce4b1ebe698df2bf3a \
  "Dataset/Human Annotated Data/train.tsv" \
  "Dataset/Human Annotated Data/validation.tsv" \
  "Dataset/Human Annotated Data/test.tsv" \
  README.md LICENSE.md

# --- provenance --------------------------------------------------------------
python3 - "$RAW" "$TODAY" <<'PY'
import hashlib, json, os, sys
raw, today = sys.argv[1], sys.argv[2]

SOURCES = [
 dict(name="ud-hindi-hdtb", dir="ud-hindi-hdtb",
      url="https://github.com/UniversalDependencies/UD_Hindi-HDTB",
      commit="71630e5d38d65fa1af8c295fc70f5f05c662498f",
      license="CC BY-NC-SA 4.0", license_file="LICENSE.txt",
      format="conllu", include=["*.conllu"], expected_script="Devanagari"),
 dict(name="ud-hindi-pud", dir="ud-hindi-pud",
      url="https://github.com/UniversalDependencies/UD_Hindi-PUD",
      commit="a68a48e09451b675e2853106474dda4dd7515510",
      license="CC BY-SA 3.0", license_file="LICENSE.txt",
      format="conllu", include=["*.conllu"], expected_script="Devanagari"),
 dict(name="hinglish-top-human", dir="hinglish-top",
      url="https://github.com/google-research-datasets/Hinglish-TOP-Dataset",
      commit="fdd3998a6573130659bfa1ce4b1ebe698df2bf3a",
      license="Apache-2.0", license_file="LICENSE.md",
      format="tsv", text_field="cs_query", include=["Dataset/**/*.tsv"],
      expected_script="Latin"),
]

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

for s in SOURCES:
    s["download_date"] = today
    base = os.path.join(raw, s["dir"])
    s["files"] = sorted(
        ({"path": os.path.relpath(os.path.join(d, f), base),
          "bytes": os.path.getsize(os.path.join(d, f)),
          "sha256": sha256(os.path.join(d, f))}
         for d, _, fs in os.walk(base) for f in fs),
        key=lambda x: x["path"])

with open(os.path.join(raw, "sources.json"), "w", encoding="utf-8") as f:
    json.dump({"sources": SOURCES}, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print("wrote", os.path.join(raw, "sources.json"))
PY
echo "done: $RAW"
