"""Tests for the canonical aircraft-name resolver.

Hermetic: builds a small temp sqlite fixture and points the resolver at it via the
`db_path` argument, so no game DB or network is needed. Run with:

    .venv/bin/python -m unittest code.tests.test_aircraft_aliases
    # or from the code/ dir:  ../.venv/bin/python -m unittest tests.test_aircraft_aliases
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aircraft_aliases as aa

# (model, icao_code) — representative rows incl. both ambiguous-ICAO pairs.
FIXTURE_ROWS = [
    ("A380-800", "A388"),
    ("A320-200", "A320"),
    ("A320neo", "A20N"),
    ("747-100B", "B741"),
    ("747-200B", "B742"),
    ("747-200F", "B74F"),   # shares B74F with 747-8F
    ("747-300", "B743"),
    ("747-400", "B744"),
    ("747-8F", "B74F"),     # shares B74F with 747-200F
    ("747-8I", "B748"),
    ("747-SP", "B74S"),
    ("An-124-100", "A124"),     # shares A124 with the V2
    ("An-124-100 V2", "A124"),
]


class AircraftAliasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(cls.db_path)
        conn.execute("CREATE TABLE aircraft (model TEXT, icao_code TEXT)")
        conn.executemany("INSERT INTO aircraft (model, icao_code) VALUES (?, ?)",
                         FIXTURE_ROWS)
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.db_path)

    def setUp(self):
        aa.reset_cache()

    def r(self, query):
        return aa.resolve(query, db_path=self.db_path)

    def test_spelling_variants_resolve_to_same_model(self):
        for q in ("A380", "a380", "A380-800", "a380 800", "A388", "a388"):
            res = self.r(q)
            self.assertEqual(res.status, "ok", q)
            self.assertEqual(res.model, "A380-800", q)
            self.assertEqual(res.icao, "A388", q)

    def test_exact_icao_with_unique_model(self):
        res = self.r("B742")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.model, "747-200B")

    def test_exact_wins_over_prefix(self):
        # "A320" is the ICAO of A320-200; must not become ambiguous with A320neo.
        res = self.r("A320")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.model, "A320-200")

    def test_family_prefix_is_ambiguous(self):
        res = self.r("747")
        self.assertEqual(res.status, "ambiguous")
        models = {c["model"] for c in res.candidates}
        self.assertIn("747-400", models)
        self.assertIn("747-8I", models)
        self.assertGreater(len(res.candidates), 2)

    def test_shared_icao_is_ambiguous(self):
        res = self.r("B74F")
        self.assertEqual(res.status, "ambiguous")
        self.assertEqual({c["model"] for c in res.candidates},
                         {"747-200F", "747-8F"})

    def test_shared_icao_a124_is_ambiguous(self):
        res = self.r("A124")
        self.assertEqual(res.status, "ambiguous")
        self.assertEqual({c["model"] for c in res.candidates},
                         {"An-124-100", "An-124-100 V2"})

    def test_full_name_disambiguates_shared_icao(self):
        res = self.r("747-8F")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.model, "747-8F")

    def test_unique_short_prefix_resolves(self):
        # "A38" is a prefix of only A380-800 in this fixture -> unique hit.
        res = self.r("A38")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.model, "A380-800")

    def test_genuine_miss(self):
        res = self.r("ZZZ999")
        self.assertEqual(res.status, "not_found")
        self.assertIsInstance(res.suggestions, list)

    def test_empty_query(self):
        res = self.r("")
        self.assertEqual(res.status, "not_found")

    def test_canonical_helper(self):
        self.assertEqual(aa.canonical("A380", db_path=self.db_path), "A380-800")
        self.assertIsNone(aa.canonical("747", db_path=self.db_path))  # ambiguous -> None
        self.assertIsNone(aa.canonical("ZZZ999", db_path=self.db_path))

    def test_catalog(self):
        cat = aa.catalog(db_path=self.db_path)
        self.assertEqual(len(cat), len(FIXTURE_ROWS))
        by_model = {c["model"]: c for c in cat}
        # ICAO appears as an alias when it differs from the model.
        self.assertIn("A388", by_model["A380-800"]["aliases"])
        # A320-200's ICAO is "A320" -> distinct from model, so listed.
        self.assertIn("A320", by_model["A320-200"]["aliases"])
        # Every entry carries model + icao keys.
        for c in cat:
            self.assertIn("model", c)
            self.assertIn("icao", c)
            self.assertIn("aliases", c)


if __name__ == "__main__":
    unittest.main()
