import unittest

from falcon_corpus.langid import identify
from falcon_corpus.normalize import match_key, normalize_text
from falcon_corpus.quality import check


class NormalizeTests(unittest.TestCase):
    def same(self, a, b):
        self.assertEqual(match_key(a), match_key(b), (a, b))

    def test_devanagari_equivalences_share_a_key(self):
        self.same("क़ोई बात", "क़ोई बात")              # precomposed vs decomposed nukta
        self.same("मल्‍लों का शहर", "मल्लों का शहर")        # ZWJ
        self.same("क्‌या है", "क्या है")                    # ZWNJ
        self.same("यह घर है ।", "यह घर है।")                     # danda spacing
        self.same("यह घर है |", "यह घर है।")                     # ASCII pipe as danda
        self.same("राम  और   श्याम", "राम और श्याम")    # NBSP / runs
        self.same("﻿राम​ और", "राम और")               # BOM, zero-width space
        self.same("२०२४ में", "2024 में")                        # Devanagari digits
        self.same("हँसना", "हंसना")                              # chandrabindu / anusvara spelling
        self.same("Café", "cafe")

    def test_different_text_has_different_key(self):
        self.assertNotEqual(match_key("10 minutes"), match_key("20 minutes"))
        self.assertNotEqual(match_key("का घर"), match_key("की घर"))

    def test_stored_text_is_conservative(self):
        t, _ = normalize_text("Set  an alarm ,  please !")
        self.assertEqual(t, "Set an alarm, please!")        # case and digits untouched
        t, _ = normalize_text("👨‍👩‍👧 family")
        self.assertIn("‍", t)                            # emoji ZWJ is not stripped
        t, _ = normalize_text("पहली पंक्ति\n\n\n\nदूसरी पंक्ति")
        self.assertEqual(t, "पहली पंक्ति\n\nदूसरी पंक्ति")       # poems keep their line structure

    def test_mojibake_is_repaired(self):
        good = "नमस्ते दुनिया, यह एक परीक्षा है।"
        for bad in (good.encode().decode("latin-1"),
                    "".join(chr(b) if b in (0x81, 0x8d, 0x8f, 0x90, 0x9d) else bytes([b]).decode("cp1252") for b in good.encode())):
            fixed, flags = normalize_text(bad)
            self.assertEqual(fixed, good)
            self.assertIn("mojibake_repaired", flags)
        self.assertEqual(normalize_text("São Paulo Ã café")[0], "São Paulo Ã café")


class LangTests(unittest.TestCase):
    def test_labels(self):
        cases = {
            "यह एक सुंदर शहर है।": "hi",
            "मैंने आज office में meeting attend की": "hi",              # English words mixed into Devanagari
            "जैसी करनी वैसी भरनी": "hi",                                  # proverb without function words
            "mere timer ko roko": "hi-Latn",
            "aaj mausam bahut accha hai": "hi-Latn",
            "Set an alarm for seven": "en",
            "Please remind me to call Karen tomorrow": "en",             # a name that is also a Hindi verb
            "Das ist nicht gut und das auch": "other",
            "आम्ही आहे आणि नाही": "other",                                # Marathi
            "2019 2020 2021": "other",
        }
        for text, want in cases.items():
            self.assertEqual(identify(text).lang, want, text)

    def test_devanagari_word_is_not_shredded(self):
        # regression: \w drops vowel signs, which made Hindi look like Marathi/Nepali
        self.assertEqual(identify("उत्तरी ग्रिड गुरुवार को फेल होते - होते बचा।").lang, "hi")


class QualityTests(unittest.TestCase):
    def drop(self, text, reason):
        self.assertEqual(check(text)[0], reason, text[:60])

    def keep(self, text):
        r = check(text)[0]
        self.assertIsNone(r, f"{text[:60]!r} dropped as {r}")

    def test_junk_is_dropped(self):
        self.drop("<div class='a'><p>नमस्ते</p><br/></div>", "markup")
        self.drop("&nbsp;&amp;&quot; नमस्ते &lt;", "markup")
        self.drop("{{Infobox|name=x}} [[श्रेणी:भारत]]", "markup")
        self.drop("function(){ var x = 1; }", "markup")
        self.drop("�����������", "garbled_encoding")
        self.drop("à¤¨à¤®à¤¸à¥à¤¤à¥‡ à¤¦à¥à¤¨à¤¿à¤¯à¤¾ à¤¹à¥ˆ", "garbled_encoding")
        self.drop("ाेंं क्् ृृ ासस ोो ्कक ाा", "garbled_encoding")
        self.drop("Click here to buy now! Call now 9876543210", "spam")
        self.drop("सट्टा मटका रिजल्ट वशीकरण विशेषज्ञ कॉल करें", "spam")
        self.drop("http://a.com http://b.com http://c.com http://d.com", "spam")
        self.drop("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", "no_content")
        self.drop("waaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaoooow hi", "spam")      # long character run
        self.drop("😀😀😀😀😀😀😀😀😀😀😀😀", "no_content")
        self.drop("बहुत अच्छा 😀😀😀😀😀😀😀😀😀😀😀😀 ★★★★★ ✔✔✔", "spam")
        self.drop("खरीदें " * 40, "repetitive")
        self.drop("Read more", "boilerplate")
        self.drop("सर्वाधिकार सुरक्षित", "boilerplate")
        self.drop("_ _ _ _ _", "no_content")
        self.drop("N/A", "placeholder")
        self.drop("lorem ipsum dolor sit amet", "placeholder")

    def test_legitimate_unusual_text_survives(self):
        self.keep("जैसी करनी वैसी भरनी")                                    # proverb
        self.keep("अब क्या")                                                 # two words
        self.keep("Shh")
        self.keep("बादल गरजे बादल गरजे\nबिजली चमके बिजली चमके\nबादल गरजे बादल गरजे\nमोर नाचे मोर नाचे")   # refrain
        self.keep("1 2 3\n4 5 6\n7 8 9")                                      # table of numbers
        self.keep("नाम\tउम्र\tशहर\nराम\t25\tदिल्ली\nश्याम\t30\tमुंबई")        # tab table
        self.keep("क्या आप कुकी खाते हैं? cookie exchange parties हैं")      # contains 'cookie'
        self.keep("उन्होंने तीन विज्ञापन किए")                               # contains 'विज्ञापन'
        self.keep("मैं A < B और C > D लिखता हूँ")                            # angle brackets that are not tags

    def test_boilerplate_lines_are_stripped_not_the_document(self):
        reason, text, flags = check("पहला वाक्य यहाँ है और यह अच्छा है।\nRead more\nदूसरा वाक्य भी यहाँ है।")
        self.assertIsNone(reason)
        self.assertNotIn("Read more", text)
        self.assertTrue(any(f.startswith("boilerplate_lines_stripped") for f in flags))


if __name__ == "__main__":
    unittest.main()
