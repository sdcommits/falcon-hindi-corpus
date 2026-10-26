import unittest
from falcon_corpus.pii import redact, verhoeff_valid


def r(t):
    return redact(t)[0]


class PhoneTests(unittest.TestCase):
    def test_mobile_formats_are_redacted(self):
        for t in ["+91 98765 43210", "+91-9876543210", "91 9876543210", "919876543210", "09876543210",
                  "98765 43210", "98765-43210", "9876543210", "098765 43210", "+91 (0) 98765 43210",
                  "987-654-3210", "9876 543 210", "0091 9876543210", "+919876543210"]:
            self.assertEqual(r(f"call {t} now"), "call <PHONE> now", t)

    def test_devanagari_digits(self):
        self.assertEqual(r("फोन ९८७६५ ४३२१० पर"), "फोन <PHONE> पर")
        self.assertEqual(r("नंबर +९१ ९८७६५४३२१०"), "नंबर <PHONE>")

    def test_landline_and_tollfree(self):
        for t in ["0172-2345678", "(011) 23456789", "022 2345 6789", "+91 22 23456789 ".strip(), "1800 123 4567", "1800-180-1234"]:
            self.assertIn("<PHONE>", r(f"ph: {t}."), t)

    def test_phone_at_sentence_end_and_comma_lists(self):
        self.assertEqual(r("नंबर 9876543210."), "नंबर <PHONE>.")
        self.assertEqual(r("9876543210,9123456789"), "<PHONE>,<PHONE>")

    def test_non_pii_numbers_survive(self):
        for t in ["साल 2024 में", "कीमत ₹1,00,000 है", "PIN 141001", "1234567890", "5123456789", "70 80 90 60 50",
                  "3.14159265358", "12.9876543210 km", "2023-10-26", "1,23,45,678", "9 12 15 18 21", "100000000000",
                  "ISBN 9780306406157", "100 200 300 400 500 600 700"]:
            self.assertEqual(r(t), t, t)


class EmailTests(unittest.TestCase):
    def test_emails(self):
        self.assertEqual(r("mail ram.kumar+x@gmail.co.in please"), "mail <EMAIL> please")
        self.assertEqual(r("ईमेल:abc_d@x.org।"), "ईमेल:<EMAIL>।")
        self.assertEqual(r("abc [at] gmail [dot] com"), "<EMAIL>")

    def test_not_emails(self):
        for t in ["@handle", "price @ 5", "a@b", "see @user_name"]:
            self.assertEqual(r(t), t)


class AadhaarPanTests(unittest.TestCase):
    def test_aadhaar(self):
        self.assertEqual(r("आधार 2345 6789 0123 है"), "आधार <AADHAAR> है")
        self.assertEqual(r("Aadhaar: 234567890123"), "Aadhaar: <AADHAAR>")
        self.assertEqual(r("uid 4567-1234-8901"), "uid <AADHAAR>")
        # a Verhoeff-valid bare number is Aadhaar even without a keyword
        valid = "2345" + "6789" + "012"
        for last in "0123456789":
            if verhoeff_valid(valid + last):
                self.assertEqual(r(valid + last), "<AADHAAR>"); break

    def test_aadhaar_negatives(self):
        self.assertEqual(r("1234 5678 9012"), "1234 5678 9012")     # cannot start with 1
        self.assertEqual(r("1234 5678 9012 3456"), "1234 5678 9012 3456")  # 16-digit card-like
        self.assertEqual(r("2345 6789 0123 4567"), "2345 6789 0123 4567")

    def test_pan(self):
        self.assertEqual(r("PAN ABCDE1234F है"), "PAN <PAN> है")
        self.assertEqual(r("ABCPD1234E"), "<PAN>")
        for t in ["abcde1234f", "ABCD1234E", "ABCDEF1234G", "AB1234", "XYZABC1234567"]:
            self.assertEqual(r(t), t)

    def test_mixed(self):
        out, c = redact("राम (ram@x.com, 98765 43210, ABCPD1234E, आधार 2345 6789 0123)")
        self.assertEqual(out, "राम (<EMAIL>, <PHONE>, <PAN>, आधार <AADHAAR>)")
        self.assertEqual(c, {"EMAIL": 1, "PHONE": 1, "PAN": 1, "AADHAAR": 1})


if __name__ == "__main__":
    unittest.main()
