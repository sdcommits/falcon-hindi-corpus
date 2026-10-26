"""Streaming readers: raw files -> RawDoc records.

Contract
--------
* Files are visited in sorted (posix) path order; records in file order, so the
  stream is deterministic for identical bytes.
* A record that cannot be turned into text is NOT silently skipped: it is
  yielded with `error` set, and the pipeline writes a ledger row for it.
* Nothing is loaded whole except the optional parquet row-group in flight.

Source attribution comes from <raw_dir>/sources.json when present (written by
download.sh); otherwise the top-level directory (or file stem) is the source.
Unknown files are handled by sniffing, because the pipeline must run on data it
has never seen.
"""
from __future__ import annotations

import csv
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterator

# Preferred text-field names, in priority order (used when none is configured).
TEXT_FIELD_CANDIDATES = (
    "text", "content", "body", "document", "doc", "sentence", "sent", "raw_text",
    "article", "message", "tweet", "comment", "review", "cs_query", "utterance",
    "story", "paragraph", "passage", "description", "title",
)
# Files that are metadata, not data, when running without sources.json.
SKIP_NAMES = re.compile(r"^(readme|license|licence|notice|contributing|authors|"
                        r"sources\.json|manifest\.json|\.ds_store)", re.I)
SKIP_EXTS = {".md", ".rst", ".png", ".jpg", ".pdf", ".log", ".xml", ".yml", ".yaml"}

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


@dataclass
class RawDoc:
    source: str
    path: str          # relative to raw_dir, posix separators
    ordinal: int       # 0-based record number inside the file
    text: str | None
    error: str | None  # None = ok; otherwise a reason code
    raw_hash: str      # sha1 of the raw record bytes (for stable ids)
    meta_lang: str | None = None  # label shipped with the source. NEVER trusted.

    @property
    def id(self) -> str:
        h = hashlib.sha1(f"{self.path}\x00{self.ordinal}\x00{self.raw_hash}".encode())
        return h.hexdigest()[:16]


def _h(b: bytes | str) -> str:
    if isinstance(b, str):
        b = b.encode("utf-8", "surrogatepass")
    return hashlib.sha1(b).hexdigest()


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def load_sources(raw_dir: str) -> list[dict]:
    p = os.path.join(raw_dir, "sources.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)["sources"]
    return []


def _all_files(raw_dir: str) -> list[str]:
    out = []
    for d, dirs, files in os.walk(raw_dir):
        dirs.sort()
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), raw_dir).replace(os.sep, "/"))
    return sorted(out)


def plan_inputs(raw_dir: str) -> list[dict]:
    """Return [{path, source, format, text_field, ...}] sorted by path."""
    sources = load_sources(raw_dir)
    files = _all_files(raw_dir)
    plan: dict[str, dict] = {}
    for s in sources:
        base = s.get("dir", s["name"]).strip("/")
        for rel in files:
            if not rel.startswith(base + "/"):
                continue
            sub = rel[len(base) + 1:]
            if any(fnmatch.fnmatch(sub, pat) for pat in s.get("include", ["*"])):
                plan[rel] = dict(path=rel, source=s["name"], format=s.get("format"),
                                 text_field=s.get("text_field"), quoting=s.get("quoting"))
    # Files not claimed by sources.json (or no sources.json at all).
    for rel in files:
        if rel in plan:
            continue
        name = os.path.basename(rel)
        ext = os.path.splitext(name)[1].lower()
        if SKIP_NAMES.match(name) or ext in SKIP_EXTS:
            continue
        if sources and any(rel.startswith(s.get("dir", s["name"]).strip("/") + "/") for s in sources):
            continue  # inside a declared source dir but not in its include list
        top = rel.split("/")[0] if "/" in rel else re.sub(r"(\.[a-z0-9]+)+$", "", name)
        plan[rel] = dict(path=rel, source=top, format=None, text_field=None, quoting=None)
    return [plan[k] for k in sorted(plan)]


def _guess_format(abs_path: str, rel: str) -> str:
    name = rel.lower()
    if name.endswith(".gz"):
        name = name[:-3]
    ext = os.path.splitext(name)[1]
    table = {".jsonl": "jsonl", ".ndjson": "jsonl", ".json": "json", ".csv": "csv",
             ".tsv": "tsv", ".txt": "txt", ".text": "txt", ".conllu": "conllu",
             ".parquet": "parquet"}
    if ext in table:
        return table[ext]
    with _open_text(abs_path) as f:  # sniff
        for line in f:
            if line.strip():
                return "jsonl" if line.lstrip().startswith("{") else "txt"
    return "txt"


def _open_bin(abs_path: str):
    if abs_path.endswith(".gz"):
        return gzip.open(abs_path, "rb")
    return open(abs_path, "rb")


def _open_text(abs_path: str):
    # strict: a bad byte raises UnicodeDecodeError, handled by callers per record
    return io.TextIOWrapper(_open_bin(abs_path), encoding="utf-8", errors="strict", newline="")


# --------------------------------------------------------------------------
# text-field selection
# --------------------------------------------------------------------------
def _pick_field(rows: list[dict], configured: str | None) -> str | None:
    if configured:
        return configured
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    for c in TEXT_FIELD_CANDIDATES:
        for k in keys:
            if k.lower() == c and any(isinstance(r.get(k), str) for r in rows):
                return k
    best, best_len = None, -1.0
    for k in keys:
        vals = [r[k] for r in rows if isinstance(r.get(k), str)]
        if vals:
            m = sum(len(v) for v in vals) / len(vals)
            if m > best_len:
                best, best_len = k, m
    return best


def _get_path(obj, field: str):
    cur = obj
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# --------------------------------------------------------------------------
# per-format iterators: yield (text|None, error|None, raw_hash)
# --------------------------------------------------------------------------
def _iter_jsonl(abs_path, cfg):
    buf: list[tuple[int, object, str]] = []
    field = cfg.get("text_field")
    chosen = None
    with _open_bin(abs_path) as f:
        pending = []
        for raw in f:
            if not raw.strip():
                continue
            rh = _h(raw)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                pending.append((None, "invalid_utf8", rh)); continue
            except json.JSONDecodeError:
                pending.append((None, "malformed_json", rh)); continue
            pending.append((obj, None, rh))
            if chosen is None and (field or len([p for p in pending if p[1] is None]) >= 50):
                chosen = _pick_field([p[0] for p in pending if isinstance(p[0], dict)], field)
                yield from _emit_json(pending, chosen); pending = []
            elif chosen is not None:
                yield from _emit_json(pending, chosen); pending = []
        if pending:
            if chosen is None:
                chosen = _pick_field([p[0] for p in pending if isinstance(p[0], dict)], field)
            yield from _emit_json(pending, chosen)


def _emit_json(items, field):
    for obj, err, rh in items:
        if err:
            yield None, err, rh, None; continue
        if isinstance(obj, str):
            yield obj, None, rh, None; continue
        if not isinstance(obj, dict):
            yield None, "unsupported_record_type", rh, None; continue
        val = _get_path(obj, field) if field else None
        lang = obj.get("lang") or obj.get("language")
        lang = lang if isinstance(lang, str) else None
        if val is None:
            yield None, "missing_text_field", rh, lang
        elif not isinstance(val, str):
            yield None, "non_string_text", rh, lang
        else:
            yield val, None, rh, lang


def _iter_json_array(abs_path, cfg):
    """Top-level JSON array, decoded element by element (bounded memory).

    A genuinely malformed element aborts the rest of the file (we cannot resync
    inside a JSON array); that is reported as one `malformed_json` record.
    """
    dec = json.JSONDecoder()
    field = cfg.get("text_field")
    chosen, items = None, []
    with _open_text(abs_path) as f:
        buf, eof = "", False

        def fill():
            nonlocal buf, eof
            more = f.read(1 << 20)
            eof = not more
            buf += more

        fill()
        i = 0
        while i < len(buf) and buf[i].isspace():
            i += 1
        if i >= len(buf) or buf[i] != "[":
            yield None, "unsupported_json_shape", _h(buf[:200]), None
            return
        i += 1
        while True:
            while True:  # skip separators, refilling when the buffer is used up
                while i < len(buf) and (buf[i].isspace() or buf[i] == ","):
                    i += 1
                if i < len(buf) or eof:
                    break
                buf, i = "", 0
                fill()
            if i >= len(buf) or buf[i] == "]":
                break
            try:
                obj, j = dec.raw_decode(buf, i)
            except json.JSONDecodeError:
                if eof:
                    yield from _emit_json(items, chosen or _pick_field([o for o, _, _ in items if isinstance(o, dict)], field)); items = []
                    yield None, "malformed_json", _h(buf[i:i + 200]), None
                    return
                buf, i = buf[i:], 0
                fill()
                continue
            items.append((obj, None, _h(buf[i:j])))
            i = j
            if chosen is None and (field or len(items) >= 50):
                chosen = _pick_field([o for o, _, _ in items if isinstance(o, dict)], field)
            if chosen is not None:
                yield from _emit_json(items, chosen); items = []
        if items:
            yield from _emit_json(items, chosen or _pick_field([o for o, _, _ in items if isinstance(o, dict)], field))


def _iter_delimited(abs_path, cfg, delim):
    field = cfg.get("text_field")
    quoting = csv.QUOTE_NONE if (cfg.get("quoting") == "none" or (delim == "\t" and cfg.get("quoting") is None)) else csv.QUOTE_MINIMAL
    with _open_text(abs_path) as f:
        reader = csv.reader(f, delimiter=delim, quoting=quoting)
        try:
            header = next(reader)
        except StopIteration:
            return
        except (csv.Error, UnicodeDecodeError):
            yield None, "malformed_header", "", None; return
        rows: list[tuple[list[str] | None, str | None, str]] = []
        idx = None

        def decide(sample):
            nonlocal idx
            recs = [dict(zip(header, r)) for r, e, _ in sample if r is not None]
            col = _pick_field(recs, field)
            idx = header.index(col) if col in header else None

        def emit(sample):
            for r, e, rh in sample:
                if e:
                    yield None, e, rh, None
                elif idx is None:
                    yield None, "missing_text_field", rh, None
                elif idx >= len(r):
                    yield None, "short_row", rh, None
                else:
                    yield r[idx], None, rh, None

        while True:
            try:
                r = next(reader)
                rows.append((r, None, _h(delim.join(r))))
            except StopIteration:
                break
            except csv.Error:
                rows.append((None, "malformed_csv", _h(str(reader.line_num))))
            except UnicodeDecodeError:
                rows.append((None, "invalid_utf8", _h(str(reader.line_num))))
                break  # the decoder state is unusable after this
            if idx is None and (field or len(rows) >= 200):
                decide(rows)
                if idx is None and field:
                    # configured column missing from the header: say so, per row
                    pass
                yield from emit(rows); rows = []
            elif idx is not None:
                yield from emit(rows); rows = []
        if rows:
            if idx is None:
                decide(rows)
            yield from emit(rows)


def _iter_txt(abs_path, cfg):
    # Mode is sniffed from the head of the file: blank-line separated blocks or one doc per line.
    mode = cfg.get("txt_mode")
    if mode is None:
        blank = total = 0
        with _open_text(abs_path) as f:
            try:
                for i, line in enumerate(f):
                    if i >= 2000:
                        break
                    total += 1
                    blank += (not line.strip())
            except UnicodeDecodeError:
                pass
        mode = "block" if total and blank / total >= 0.15 else "line"
    block: list[str] = []
    with _open_bin(abs_path) as f:
        for raw in f:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                yield None, "invalid_utf8", _h(raw), None
                continue
            if mode == "line":
                if line.strip():
                    yield line.rstrip("\r\n"), None, _h(raw), None
            else:
                if line.strip():
                    block.append(line.rstrip("\r\n"))
                elif block:
                    t = "\n".join(block); yield t, None, _h(t), None; block = []
        if block:
            t = "\n".join(block); yield t, None, _h(t), None


def _iter_conllu(abs_path, cfg):
    """Universal Dependencies: one document per sentence, text = '# text = ...'."""
    text = None
    have = False
    h = hashlib.sha1()
    with _open_bin(abs_path) as f:
        for raw in f:
            h.update(raw)
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                line = "\x00bad"
            if not line.strip():
                if have:
                    yield (text, None, h.hexdigest(), None) if text is not None else (None, "conllu_no_text", h.hexdigest(), None)
                    text, have, h = None, False, hashlib.sha1()
                else:
                    h = hashlib.sha1()
                continue
            have = True
            if line.startswith("# text = "):
                text = line[len("# text = "):]
            elif line.startswith("# text ="):
                text = line[len("# text ="):].lstrip()
        if have:
            yield (text, None, h.hexdigest(), None) if text is not None else (None, "conllu_no_text", h.hexdigest(), None)


def _iter_parquet(abs_path, cfg):
    try:
        import pyarrow.parquet as pq
    except ImportError:
        yield None, "parquet_needs_pyarrow", _h(abs_path), None
        return
    pf = pq.ParquetFile(abs_path)
    field = cfg.get("text_field")
    names = pf.schema_arrow.names
    if not field:
        sample = pf.read_row_group(0).to_pylist()[:100] if pf.num_row_groups else []
        field = _pick_field(sample, None)
    for batch in pf.iter_batches(batch_size=4096):
        for row in batch.to_pylist():
            val = row.get(field) if field else None
            rh = _h(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
            if val is None:
                yield None, "missing_text_field", rh, None
            elif not isinstance(val, str):
                yield None, "non_string_text", rh, None
            else:
                yield val, None, rh, None


_ITERATORS = {"jsonl": _iter_jsonl, "json": _iter_json_array, "txt": _iter_txt,
              "conllu": _iter_conllu, "parquet": _iter_parquet,
              "csv": lambda p, c: _iter_delimited(p, c, ","),
              "tsv": lambda p, c: _iter_delimited(p, c, "\t")}


def iter_file(raw_dir: str, entry: dict) -> Iterator[RawDoc]:
    abs_path = os.path.join(raw_dir, entry["path"])
    fmt = entry.get("format") or _guess_format(abs_path, entry["path"])
    it = _ITERATORS[fmt]
    n = 0
    try:
        for text, err, rh, lang in it(abs_path, entry):
            yield RawDoc(entry["source"], entry["path"], n, text, err, rh, lang)
            n += 1
    except (OSError, EOFError, gzip.BadGzipFile, ValueError, csv.Error) as e:
        # truncated gzip, I/O error mid-file ...: record it and move on to the next file
        yield RawDoc(entry["source"], entry["path"], n, None,
                     f"file_error:{type(e).__name__}", _h(f"{entry['path']}|{n}|{e}"))


def file_sha256(abs_path: str) -> str:
    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
