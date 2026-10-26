#!/usr/bin/env bash
# Usage: ./run.sh <raw_dir> <out_dir> [options]      (see ./run.sh --help)
# Offline by design: reads only <raw_dir>. Fetching is download.sh's job.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$#" -lt 2 ] && [ "${1:-}" != "--help" ] && [ "${1:-}" != "-h" ]; then
  echo "usage: $0 <raw_dir> <out_dir> [options]" >&2; exit 2
fi
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m falcon_corpus "$@"
