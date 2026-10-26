import csv
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from falcon_corpus import pipeline, readers
from falcon_corpus.translit import skeleton
from tests import fixtures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = ["ledger.csv", "eval.jsonl", "contamination.jsonl", "manifest.json"]


def run_cli(raw, out, *args, env=None):
    e = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src") + os.pathsep + ROOT)
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "falcon_corpus", raw, out, *args], env=e, capture_output=True, text=True)


def read_ledger(out):
    with open(os.path.join(out, "ledger.csv"), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def chunk_mtimes(out):
    root = os.path.join(out, "_work", "pass1")
    return {os.path.join(d, n): os.stat(os.path.join(d, n)).st_mtime_ns
            for d, _, files in os.walk(root) for n in files if n.startswith("chunk-")}


def read_clean(out):
    rows = []
    d = os.path.join(out, "clean")
    for name in sorted(os.listdir(d)):
        with gzip.open(os.path.join(d, name), "rt", encoding="utf-8") as f:
            rows += [json.loads(l) for l in f]
    return rows


def digest(out):
    h = {}
    files = OUTPUTS + ["clean/" + n for n in sorted(os.listdir(os.path.join(out, "clean")))]
    for n in files:
        with open(os.path.join(out, n), "rb") as f:
            h[n] = hashlib.sha256(f.read()).hexdigest()
    return h


class FixtureSanity(unittest.TestCase):
    def test_latin_spellings_match_devanagari_skeletons(self):
        for deva, latin in fixtures.VOCAB + fixtures.FUNC + [fixtures.END]:
            self.assertEqual(skeleton(deva), skeleton(latin), (deva, latin))


class JunkAndFormats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.raw, cls.out = os.path.join(cls.tmp, "raw"), os.path.join(cls.tmp, "out")
        cls.exp = fixtures.build_junk_corpus(cls.raw)
        r = run_cli(cls.raw, cls.out, "--eval-size", "0")
        assert r.returncode == 0, r.stderr
        cls.ledger = read_ledger(cls.out)
        cls.by = {(row["path"], int(row["ordinal"])): row for row in cls.ledger}
        cls.clean = {c["id"]: c for c in read_clean(cls.out)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def row(self, name):
        key = next(k for k, v in self.exp.items() if v == name)
        return self.by[key]

    def test_one_ledger_row_per_input_record_and_unique_ids(self):
        n = sum(1 for e in readers.plan_inputs(self.raw) for _ in readers.iter_file(self.raw, e))
        self.assertEqual(len(self.ledger), n)
        self.assertEqual(len({r["id"] for r in self.ledger}), n)

    def test_malformed_records_are_counted_not_fatal(self):
        self.assertEqual((self.row("bad_json")["stage"], self.row("bad_json")["reason"]), ("parse", "malformed_json"))
        self.assertEqual(self.row("bad_utf8")["reason"], "invalid_utf8")
        self.assertEqual(self.row("non_string")["reason"], "non_string_text")
        self.assertEqual(self.row("missing_field")["reason"], "missing_text_field")
        self.assertEqual(self.row("last_good")["status"], "kept")           # parsing continued after the bad lines
        with open(os.path.join(self.out, "manifest.json")) as fh:
            m = json.load(fh)
        self.assertEqual(m["counts"]["skipped_malformed"]["malformed_json"], 1)

    def test_junk_is_dropped_with_the_right_stage(self):
        for name, stage, reason in [("spam", "filter", "spam"), ("markup", "filter", "markup"), ("garbled", "filter", "garbled_encoding"),
                                    ("english", "lang", "lang=en"), ("marathi", "lang", "lang=other"), ("empty", "filter", "empty"),
                                    ("whitespace", "filter", "empty")]:
            r = self.row(name)
            self.assertEqual((r["status"], r["stage"]), ("dropped", stage), name)
            self.assertTrue(r["reason"].startswith(reason), (name, r["reason"]))

    def test_legitimate_unusual_text_is_kept(self):
        for name in ("proverb", "table", "mojibake_repaired", "non_pii_numbers"):
            self.assertEqual(self.row(name)["status"], "kept", name)
        self.assertEqual(self.clean[self.row("table")["id"]]["lang"], "other")        # language-neutral, labelled honestly
        self.assertIn("mojibake_repaired", self.row("mojibake_repaired")["edits"])
        self.assertEqual(self.clean[self.row("mojibake_repaired")["id"]]["text"], "नमस्ते दुनिया, यह एक परीक्षा है।")

    def test_pii_is_redacted_and_other_numbers_survive(self):
        t = self.clean[self.row("pii")["id"]]["text"]
        self.assertEqual(t, "मेरा नंबर <PHONE> है और ईमेल <EMAIL>, PAN <PAN>, आधार <AADHAAR> है।")
        n = self.clean[self.row("non_pii_numbers")["id"]]["text"]
        self.assertEqual(n, "कीमत ₹1,00,000 है और साल 2024 है, PIN 141001, नंबर 1234567890 और 5123456789।")
        everything = json.dumps(list(self.clean.values()), ensure_ascii=False)
        for leaked in ("9876543210", "98765 43210", "ram@example.com", "ABCPD1234E", "2345 6789 0123"):
            self.assertNotIn(leaked, everything)

    def test_every_input_format_is_read(self):
        for path in ("gz_docs.jsonl.gz", "pairs.csv", "plain.tsv", "blocks.txt", "array.json", "tree.conllu"):
            statuses = [r["status"] for (p, _), r in self.by.items() if p == path]
            self.assertTrue(statuses, path)
        kept = lambda p: sum(1 for (pp, _), r in self.by.items() if pp == p and r["status"] == "kept")
        self.assertEqual(kept("gz_docs.jsonl.gz"), 2)
        self.assertEqual(kept("pairs.csv"), 2)         # embedded newline survives, quoted field intact
        self.assertEqual(kept("plain.tsv"), 2)         # text column auto-detected
        self.assertEqual(kept("blocks.txt"), 3)        # blank-line separated blocks
        self.assertEqual(kept("array.json"), 2)
        self.assertEqual(kept("tree.conllu"), 2)
        bad = [r for (p, _), r in self.by.items() if p == "tree.conllu" and r["status"] == "dropped"]
        self.assertEqual([r["reason"] for r in bad], ["conllu_no_text"])


def doc(text, eval_id=None, cfg=None):
    """A pass-1 record for `text`, as the pipeline would produce it."""
    cfg = cfg or pipeline.Config()
    r = pipeline.process_doc(readers.RawDoc("t", "t.jsonl", abs(hash(text)) % 10**6, text, None, hashlib.sha1(text.encode()).hexdigest()), cfg)
    assert r["status"] == "candidate", (text, r)
    if eval_id:
        r["eval_id"] = eval_id
    return r


class EvalIndexMethods(unittest.TestCase):
    """The four leak methods, on explicit documents."""
    DEVA = "मेरा घर बहुत बड़ा है और यह शहर का सबसे सुंदर गाँव है।"
    LATN = "mera ghar bahut bada hai aur yeh shahar ka sabse sundar gaon hai"

    def method(self, eval_text, train_text):
        idx = pipeline.EvalIndex([doc(eval_text, "eval-1")], pipeline.Config())
        hits, rejected = idx.check(doc(train_text))
        return (hits["eval-1"][0] if hits else None), rejected

    def test_exact(self):
        self.assertEqual(self.method(self.DEVA, self.DEVA)[0], "exact")

    def test_normalized_copy_with_different_raw_bytes(self):
        variant = "मेरा\u00a0घर  बहुत बड़ा है और यह शहर का सबसे सुं\u200dदर गाँव है |"
        self.assertNotEqual(variant, self.DEVA)
        self.assertEqual(self.method(self.DEVA, variant)[0], "normalized")
        self.assertEqual(self.method("Set an alarm, please ka kaam", "set an alarm please ka kaam")[0], "normalized")   # case + punctuation

    def test_near_copy_one_character_edit(self):
        self.assertEqual(self.method(self.DEVA, self.DEVA.replace("सुंदर", "सुंदार"))[0], "near")
        self.assertEqual(self.method("timer par kitna samay bach hai", "timer par kitna samay bacha hai")[0], "near")   # short string, J<0.8

    def test_translit_both_directions(self):
        self.assertEqual(self.method(self.DEVA, self.LATN)[0], "translit")
        self.assertEqual(self.method(self.LATN, self.DEVA)[0], "translit")
        iso = "merā ghar bahut baṛā hai aur yaha śahara kā sabase suṁdara gāṁva hai"          # ISO-15919 style, as in UD treebanks
        self.assertEqual(self.method(self.DEVA, iso)[0], "translit")

    def test_not_leaks(self):
        self.assertIsNone(self.method(self.DEVA, "तुम्हारा स्कूल बहुत छोटा है और वह नदी के पास है।")[0])
        method, rejected = self.method("mere timer ko 10 minutes ke liye set karo", "mere timer ko 20 minutes ke liye set karo")
        self.assertIsNone(method)                                   # same template, different number: a sibling, not a copy
        self.assertEqual([r[4] for r in rejected], ["digits_differ"])

    def test_same_script_similarity_is_not_reported_as_translit(self):
        method, _ = self.method(self.LATN, "tera ghar bahut chhota hai aur woh gaon ka sabse purana ghar hai")
        self.assertNotEqual(method, "translit")


class Contamination(unittest.TestCase):
    """End to end on a corpus where every sentence has planted copies."""
    DEVA = {"base_hi", "exact", "normalized"}                       # identical after normalisation
    LATN = {"base_latn", "translit_a"}                              # identical after normalisation

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.raw, cls.out = os.path.join(cls.tmp, "raw"), os.path.join(cls.tmp, "out")
        cls.sents, cls.tags = fixtures.build_leak_corpus(cls.raw)
        r = run_cli(cls.raw, cls.out, "--eval-size", "40")
        assert r.returncode == 0, r.stderr
        cls.ledger = read_ledger(cls.out)
        cls.by_id = {r["id"]: r for r in cls.ledger}
        cls.by = {(row["path"], int(row["ordinal"])): row for row in cls.ledger}
        cls.eval = read_jsonl(os.path.join(cls.out, "eval.jsonl"))
        cls.contam = {c["eval_id"]: c for c in read_jsonl(os.path.join(cls.out, "contamination.jsonl"))}
        cls.clean = read_clean(cls.out)
        cls.id_tag = {row["id"]: cls.tags[(p, o)] for (p, o), row in cls.by.items() if (p, o) in cls.tags}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_eval_has_both_scripts_and_is_not_in_clean(self):
        self.assertEqual(len(self.eval), 40)
        self.assertEqual({e["lang"] for e in self.eval}, {"hi", "hi-Latn"})
        self.assertFalse({c["id"] for c in self.clean} & {e["id"] for e in self.eval})

    def test_single_hop_planted_copies_are_found_and_removed(self):
        # which sibling variants MUST be caught, given the variant that became the eval item
        must = {**{v: self.DEVA | {"near"} | self.LATN for v in self.DEVA},       # Deva eval: copies, typo, romanisation
                **{v: self.DEVA | self.LATN for v in self.LATN},                 # Latin eval: Latin copies and the Devanagari original
                "near": self.DEVA}                                                # typo'd eval: its un-typo'd originals
        checked = 0
        for e in self.eval:
            base, variant = self.id_tag[e["id"]]
            if base >= 140:
                continue                                                          # last 20 sentences have no planted copies
            for sid, (b, v) in self.id_tag.items():
                if b == base and sid != e["id"] and v in must[variant]:
                    checked += 1
                    led = self.by_id[sid]
                    self.assertEqual((led["status"], led["stage"]), ("dropped", "contamination"), (variant, v, e["text"]))
                    self.assertIn(sid, self.contam[e["eval_id"]]["train_ids"])
        self.assertGreater(checked, 100)

    def test_contamination_outputs_are_consistent_with_the_ledger(self):
        eval_doc_ids = {e["id"] for e in self.eval}
        flagged = {i for c in self.contam.values() for i in c["train_ids"]}
        in_ledger = {r["id"] for r in self.ledger if r["stage"] == "contamination"}
        self.assertTrue(in_ledger <= flagged)
        self.assertTrue(all(self.by_id[i]["status"] == "dropped" for i in flagged if self.by_id[i]["stage"] == "contamination"))
        for r in self.ledger:
            if r["stage"] == "contamination":
                self.assertIn(r["dup_of"], eval_doc_ids)
        for c in self.contam.values():
            self.assertIn(c["method"], c["methods"])

    def test_no_clean_document_matches_an_eval_item(self):
        from falcon_corpus.normalize import match_key
        keys = {match_key(e["text"]) for e in self.eval}
        for c in self.clean:
            self.assertNotIn(match_key(c["text"]), keys)

    def test_digit_difference_is_not_a_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, out = os.path.join(tmp, "raw"), os.path.join(tmp, "out")
            rows = [{"text": f"mere timer ko {n} minutes ke liye set karo aur bata do kya hai"} for n in range(10, 30)]
            rows += [{"text": f"aaj {n} baje ka alarm lagao aur mujhe yaad dilao ki kaam hai"} for n in range(5, 25)]
            fixtures.write_jsonl(os.path.join(raw, "t.jsonl"), rows)
            self.assertEqual(run_cli(raw, out, "--eval-size", "20", "--near-threshold", "0.8").returncode, 0)
            led = read_ledger(out)
            self.assertEqual([r for r in led if r["stage"] == "contamination"], [])
            self.assertEqual([r for r in led if r["stage"] == "dedup"], [])


class Determinism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.raw = os.path.join(cls.tmp, "raw")
        fixtures.build_leak_corpus(cls.raw)
        fixtures.build_junk_corpus(cls.raw)
        cls.args = ("--chunk-size", "120", "--eval-size", "40")
        r = run_cli(cls.raw, os.path.join(cls.tmp, "ref"), *cls.args)
        assert r.returncode == 0, r.stderr
        cls.ref = digest(os.path.join(cls.tmp, "ref"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_rerun_is_identical_and_idempotent(self):
        out = os.path.join(self.tmp, "again")
        self.assertEqual(run_cli(self.raw, out, *self.args).returncode, 0)
        self.assertEqual(digest(out), self.ref)
        self.assertEqual(run_cli(self.raw, out, *self.args).returncode, 0)        # second run on a finished dir
        self.assertEqual(digest(out), self.ref)

    def test_killed_in_pass_1_then_restarted_matches_uninterrupted(self):
        out = os.path.join(self.tmp, "kill1")
        r = run_cli(self.raw, out, *self.args, env={"FALCON_CRASH_AFTER_CHUNKS": "2"})
        self.assertEqual(r.returncode, 137, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "ledger.csv")))          # nothing half-published
        before = chunk_mtimes(out)
        self.assertEqual(len(before), 2)                                           # two chunks were durably written before the kill
        r = run_cli(self.raw, out, *self.args)
        self.assertEqual(r.returncode, 0, r.stderr)
        after = chunk_mtimes(out)
        self.assertGreater(len(after), len(before))
        for path, mtime in before.items():
            self.assertEqual(after[path], mtime, "a chunk finished before the kill was recomputed")   # resumed, not redone
        self.assertEqual(digest(out), self.ref)

    def test_killed_in_pass_2_then_restarted_matches_uninterrupted(self):
        out = os.path.join(self.tmp, "kill2")
        self.assertEqual(run_cli(self.raw, out, *self.args, env={"FALCON_CRASH_IN_PASS2": "300"}).returncode, 137)
        self.assertEqual(run_cli(self.raw, out, *self.args).returncode, 0)
        self.assertEqual(digest(out), self.ref)
        ids = [r["id"] for r in read_ledger(out)]
        self.assertEqual(len(ids), len(set(ids)))                                  # no duplicated rows
        clean_ids = [c["id"] for c in read_clean(out)]
        self.assertEqual(len(clean_ids), len(set(clean_ids)))

    def test_parallel_pass_1_gives_identical_output(self):
        out = os.path.join(self.tmp, "par")
        self.assertEqual(run_cli(self.raw, out, *self.args, "--workers", "2").returncode, 0)
        self.assertEqual(digest(out), self.ref)

    def test_changed_input_invalidates_stale_work(self):
        raw2, out = os.path.join(self.tmp, "raw2"), os.path.join(self.tmp, "stale")
        shutil.copytree(self.raw, raw2)
        run_cli(raw2, out, *self.args)
        with open(os.path.join(raw2, "blocks.txt"), "a", encoding="utf-8") as f:
            f.write("\nचौथा अनुच्छेद नया जोड़ा गया है।\n")
        self.assertEqual(run_cli(raw2, out, *self.args).returncode, 0)
        ref2 = os.path.join(self.tmp, "ref2")
        run_cli(raw2, ref2, *self.args)
        self.assertEqual(digest(out), digest(ref2))


if __name__ == "__main__":
    unittest.main()
