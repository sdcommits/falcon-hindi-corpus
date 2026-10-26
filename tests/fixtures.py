"""Builders for synthetic raw directories used by the integration tests."""
import gzip
import json
import os
import random

# (Devanagari, Latin spelling). Latin forms follow the skeleton rules in translit.py;
# test_pipeline asserts skeleton(deva) == skeleton(latin) for every pair.
VOCAB = [
    ("मेरा", "mera"), ("तुम्हारा", "tumhara"), ("घर", "ghar"), ("बहुत", "bahut"), ("बड़ा", "bada"), ("छोटा", "chhota"),
    ("शहर", "shahar"), ("किताब", "kitab"), ("पानी", "pani"), ("खाना", "khana"), ("स्कूल", "skool"), ("दोस्त", "dost"),
    ("सुबह", "subah"), ("शाम", "shaam"), ("रात", "raat"), ("बाजार", "bazar"), ("गाँव", "gaanv"), ("नदी", "nadi"),
    ("पहाड़", "pahad"), ("बच्चा", "bachcha"), ("लड़का", "ladka"), ("लड़की", "ladki"), ("काम", "kaam"), ("समय", "samay"),
    ("मौसम", "mausam"), ("सड़क", "sadak"), ("गाड़ी", "gadi"), ("दुकान", "dukan"), ("पैसा", "paisa"), ("रोज", "roz"),
    ("अच्छा", "achha"), ("नया", "naya"), ("पुराना", "purana"), ("सुंदर", "sundar"), ("तेज", "tez"), ("धीरे", "dhire"),
    ("बगीचा", "bagicha"), ("फूल", "phool"), ("पेड़", "ped"), ("चिड़िया", "chidiya"), ("आसमान", "aasman"), ("धूप", "dhoop"),
    ("बारिश", "barish"), ("हवा", "hawa"), ("रास्ता", "rasta"), ("मंदिर", "mandir"), ("त्योहार", "tyohar"), ("मिठाई", "mithai"),
    ("चाय", "chay"), ("दूध", "doodh"), ("रोटी", "roti"), ("सब्जी", "sabzi"), ("कपड़ा", "kapda"), ("जूता", "joota"),
    ("खेल", "khel"), ("गाना", "gana"), ("कहानी", "kahani"), ("चित्र", "chitra"), ("खिड़की", "khidki"), ("दरवाजा", "darwaza"),
]
FUNC = [("का", "ka"), ("की", "ki"), ("के", "ke"), ("में", "mein"), ("और", "aur"), ("यह", "yeh"), ("नहीं", "nahin")]
END = ("है", "hai")


def make_sentences(n, seed=7, length=8):
    rnd = random.Random(seed)
    seen, out = set(), []
    while len(out) < n:
        words = [rnd.choice(VOCAB) for _ in range(length)]
        for pos in (2, 5):                                   # sprinkle function words
            words[pos] = rnd.choice(FUNC)
        words.append(END)
        deva = " ".join(w[0] for w in words) + "।"
        if deva in seen:
            continue
        seen.add(deva)
        out.append((deva, " ".join(w[1] for w in words)))
    return out


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_leak_corpus(raw, n_base=160, n_planted=140):
    """Every base sentence appears as Devanagari AND romanised text; the first n_planted
    also get leak variants. Returns {(relpath, ordinal): (base_index, variant)}."""
    sents = make_sentences(n_base)
    tags = {}
    files = {"base_hi.jsonl": [], "base_latn.jsonl": [], "copies.jsonl": []}

    def add(fname, text, base, variant):
        tags[(f"raw/{fname}" if False else fname, len(files[fname]))] = (base, variant)
        files[fname].append({"text": text})

    for i, (deva, latin) in enumerate(sents):
        add("base_hi.jsonl", deva, i, "base_hi")
        add("base_latn.jsonl", latin.capitalize() + ".", i, "base_latn")
    for i, (deva, latin) in enumerate(sents[:n_planted]):
        add("copies.jsonl", deva, i, "exact")                                               # byte-identical
        words = deva[:-1].split(" ")
        norm = " ".join(words[:3]) + "  " + " ".join(words[3:]).replace("क्", "क्‍", 1) + " |"
        add("copies.jsonl", norm, i, "normalized")                                         # NBSP, extra space, ZWJ, pipe-danda
        mid = len(words) // 2
        w = words[mid]
        typo = " ".join(words[:mid] + [w[:-1] if len(w) > 3 else w + "ा"] + words[mid + 1:]) + "।"
        add("copies.jsonl", typo, i, "near")                                                # one-character edit
        add("copies.jsonl", latin.upper(), i, "translit_a")                                 # Latin, different case: normalized vs base_latn
    for fname, rows in files.items():
        write_jsonl(os.path.join(raw, fname), rows)
    return sents, tags


def build_junk_corpus(raw):
    """One file per input format, with every kind of junk. Returns {(relpath, ordinal): expectation}."""
    good = "नमस्ते दुनिया, यह एक परीक्षा है।"
    moji = good.encode().decode("latin-1")
    rows = [
        ("spam", {"text": "यहाँ क्लिक करें अभी खरीदें मुफ्त डाउनलोड सट्टा मटका कॉल करें 9876543210"}),
        ("markup", {"text": "<div class='a'><p>नमस्ते दुनिया यह एक परीक्षा है</p><br/></div>"}),
        ("mojibake_repaired", {"text": moji}),
        ("garbled", {"text": "��� ��� मेरा घर"}),
        ("pii", {"text": "मेरा नंबर 98765 43210 है और ईमेल ram@example.com, PAN ABCPD1234E, आधार 2345 6789 0123 है।"}),
        ("proverb", {"text": "जैसी करनी वैसी भरनी"}),
        ("table", {"text": "1 2 3\n4 5 6\n7 8 9"}),
        ("english", {"text": "This is an English sentence about the weather today."}),
        ("marathi", {"text": "आम्ही आहे आणि नाही आणि हे आहे"}),
        ("empty", {"text": ""}),
        ("whitespace", {"text": "   \n  "}),
        ("non_pii_numbers", {"text": "कीमत ₹1,00,000 है और साल 2024 है, PIN 141001, नंबर 1234567890 और 5123456789।"}),
        ("non_string", {"text": 12345}),
        ("missing_field", {"body_text": "कोई टेक्स्ट नहीं"}),
    ]
    path = os.path.join(raw, "junk.jsonl")
    os.makedirs(raw, exist_ok=True)
    exp = {}
    with open(path, "wb") as f:
        for i, (name, rec) in enumerate(rows):
            f.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
            exp[("junk.jsonl", i)] = name
        f.write(b"{this is not json\n"); exp[("junk.jsonl", len(rows))] = "bad_json"
        f.write(b'{"text": "\xff\xfe invalid utf8"}\n'); exp[("junk.jsonl", len(rows) + 1)] = "bad_utf8"
        f.write(json.dumps({"text": "यह आखिरी ठीक वाक्य है।"}, ensure_ascii=False).encode() + b"\n")
        exp[("junk.jsonl", len(rows) + 2)] = "last_good"

    # other formats, each with 2 valid documents
    with gzip.open(os.path.join(raw, "gz_docs.jsonl.gz"), "wt", encoding="utf-8") as f:
        f.write(json.dumps({"content": "गुजरे हुए कल की बात है यह।"}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"content": "aaj mausam bahut accha hai aur hawa thandi hai"}) + "\n")
    with open(os.path.join(raw, "pairs.csv"), "w", encoding="utf-8", newline="") as f:
        f.write('id,comment,score\n1,"पहली पंक्ति\nदूसरी पंक्ति है यहाँ",5\n2,"mujhe yeh gana bahut pasand hai",4\n')
    with open(os.path.join(raw, "plain.tsv"), "w", encoding="utf-8") as f:     # no known column name: the longest-text column wins
        f.write("n\tlabel\tsentence_blob\n1\tx\tबारिश का मौसम बहुत सुहावना होता है।\n2\ty\tkal ka kaam aaj kar lo yeh theek nahi hai\n")
    with open(os.path.join(raw, "blocks.txt"), "w", encoding="utf-8") as f:
        f.write("पहला अनुच्छेद है और यह लंबा है।\nदूसरी पंक्ति भी वहीं है।\n\nदूसरा अनुच्छेद अलग है यहाँ।\n\nतीसरा अनुच्छेद भी है।\n")
    with open(os.path.join(raw, "array.json"), "w", encoding="utf-8") as f:
        json.dump([{"text": "यह एक जेसन सूची का पहला वाक्य है।"}, {"text": "यह दूसरा वाक्य है जो सूची में है।"}], f, ensure_ascii=False)
    with open(os.path.join(raw, "tree.conllu"), "w", encoding="utf-8") as f:
        f.write("# sent_id = 1\n# text = यह घर है ।\n1\tयह\t_\n\n# sent_id = 2\n# text = मेरा नाम राम है ।\n1\tमेरा\t_\n\n# sent_id = 3\n1\tx\t_\n\n")
    return exp
