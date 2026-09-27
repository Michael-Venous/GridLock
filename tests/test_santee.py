"""Checks for pipeline/parse_santee.py. Run: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import parse_santee  # noqa: E402
from common import RAW  # noqa: E402

LIST = """      Transmission Projects 2026-2030
                          Project Title                            In-service Date
Reconductor Purrysburg - Mcintosh 230 kV tie lines                        5/1/2026
Bluffton Station Improvements                                            11/1/2026
Conway 230 kV Switching Station                                          12/1/2025
Marion-Conway 230 kV Line                                                12/1/2025
Johns Island – Queensboro (DESC) 115 kV Line                                 TBD
                                                                                  50
"""
RECONDUCTOR = """Reconductor Purrysburg-McIntosh 230 kV Tie Lines
Project Description
Reconductor the existing Purrysburg-McIntosh 230 kV tie lines with bundled 1272 "Bittern" ACSS conductor.
Project Need
Reconductoring will mitigate thermal loading exceedances.
Project Status
Committed
Planned In-Service Date
December 2026
                                                                                  51
"""
CONWAY = """Conway 230 kV Switching Station and Marion-Conway 230 kV Line
Project Description
Fold the Hemingway-Red Bluff 230 kV Line into the new Conway 230 kV Switching Station. Construct a 230 kV line
approximately 34 miles in length from the Marion 230-115-69kV Substation to the Conway 230 kV Switching Station.
Project Need
Studies indicate thermal loading.
Project Status In Progress
Planned In-Service Date December 2025
                                                                                  52
"""
COMMITTED = """Committed Transmission Facilities
                          Project Title                            In-service Date
Bluffton Station Improvements                                            3/1/2027
"""


class TitleTests(unittest.TestCase):
    def test_station_names_come_from_titles(self):
        for title, expected in (
            ("Reconductor Purrysburg - Mcintosh 230 kV tie lines", ["Purrysburg", "Mcintosh"]),
            ("Bluffton Station Improvements", ["Bluffton"]),
            ("Varnville 230-115-69 kV Substation Upgrades", ["Varnville"]),
            ("Rebuild Kingstree-Hemingway 115 kV Line as a Double Circuit 230/115 kV Line", ["Kingstree", "Hemingway"]),
            ("Cross - Jefferies #2 230 kV Line", ["Cross", "Jefferies"]),
            ("Carolina Forest 230-115 kV Substation: Add Transformer", ["Carolina Forest"]),
            ("Install 2nd Wassamassaw xfmr at Wassamassaw", ["Wassamassaw"]),
            ("Varnville to Nixville tap 69 kV Rebuild to 115 kV", ["Varnville", "Nixville"]),
            ("Johns Island – Queensboro (DESC) 115 kV Line", ["Johns Island", "Queensboro"]),
            ("Wassamassaw-Pringletown #1 and #2 115 kV Line", ["Wassamassaw", "Pringletown"]),
        ):
            with self.subTest(title=title):
                self.assertEqual(parse_santee.endpoint_names(title), expected)

    def test_keys_ignore_voltage_punctuation_and_circuit_numbers_but_not_the_work(self):
        self.assertEqual(parse_santee.key_of("Reconductor Purrysburg - Mcintosh 230 kV tie lines"),
                         parse_santee.key_of("Reconductor Purrysburg-McIntosh 230 kV Tie Lines"))
        self.assertNotEqual(parse_santee.key_of("Varnville 230-115 kV substation"),
                            parse_santee.key_of("Varnville 230-115 kV transformer replacement"))

    def test_a_slide_covering_two_rows_serves_both_but_a_longer_title_does_not_claim_a_shorter_one(self):
        slides = [{"title": "Conway 230 kV Switching Station and Marion-Conway 230 kV Line"},
                  {"title": "Indian Field – Wassamassaw 230 kV Line"}]
        self.assertIs(parse_santee.own_slide("Conway 230 kV Switching Station", slides), slides[0])
        self.assertIs(parse_santee.own_slide("Marion-Conway 230 kV Line", slides), slides[0])
        self.assertIsNone(parse_santee.own_slide("Indian Field 230-115 kV Substation", slides))


class ParseTests(unittest.TestCase):
    def parse(self):
        edition = {"edition": "2026-2030", "file": "x.pdf", "url": "https://example.test/deck.pdf", "date": "2026-03-11"}
        pages = [COMMITTED, "", LIST, RECONDUCTOR, CONWAY, "Santee Cooper\nTransmission Expansion Plans\nQuestions?"]
        with patch.object(parse_santee, "EDITIONS", {"2026-2030": edition}), patch.object(parse_santee, "pdf_pages", return_value=pages), \
                patch.object(parse_santee, "dump"):
            return parse_santee.parse("2026-2030")

    def test_rows_join_their_slides_and_date_disagreements_are_reported(self):
        recs, notes = self.parse()
        self.assertEqual(notes, [])
        by = {r["project_id"]: r for r in recs}
        rec = by["Row 1"]
        self.assertEqual((rec["uid"], rec["utility"], rec["state"]), ("SCPSA:RECONDUCTOR-PURRYSBURG-MCINTOSH-TIE-LINES", "SCPSA", "SC"))
        self.assertEqual(rec["isd"], "2026-05-01")          # the list's date is kept...
        self.assertIn("the list (slide 3) gives 5/1/2026, but the project's own slide 4 says December 2026",
                      [i["msg"] for i in rec["issues"]])   # ...and the slide's date is reported
        self.assertEqual(rec["endpoint_names"], ["Purrysburg", "McIntosh"])   # spelled as the project's slide spells it
        self.assertEqual((rec["status"], rec["source"]["page"]), ("Committed", 4))
        self.assertIsNone(rec["cost"]["total"])
        self.assertIsNone(rec["window"]["start"])

    def test_rows_without_a_slide_say_so_and_other_tables_are_cross_checked(self):
        recs, _ = self.parse()
        bluffton = next(r for r in recs if r["name"].startswith("Bluffton"))
        self.assertIsNone(bluffton["status"])
        msgs = [i["msg"] for i in bluffton["issues"]]
        self.assertTrue(any("no project slide" in m for m in msgs))
        self.assertTrue(any("slide 1's table gives 3/1/2027" in m for m in msgs))

    def test_combined_slide_miles_go_to_the_line_only_and_tbd_is_flagged(self):
        recs, _ = self.parse()
        by = {r["name"]: r for r in recs}
        self.assertEqual(by["Marion-Conway 230 kV Line"]["miles"], 34.0)
        self.assertIsNone(by["Conway 230 kV Switching Station"]["miles"])
        self.assertEqual(by["Conway 230 kV Switching Station"]["status"], "In Progress")
        tbd = by["Johns Island – Queensboro (DESC) 115 kV Line"]
        self.assertIsNone(tbd["isd"])
        self.assertTrue(any("TBD" in i["msg"] for i in tbd["issues"]))


@unittest.skipUnless((RAW / "santee_2026-2030.pdf").exists(), "SCRTP decks not downloaded (run pipeline/fetch.py)")
class PublishedDeckTests(unittest.TestCase):
    def test_each_edition_reads_completely(self):
        counts = {ed: len(parse_santee.parse(ed)[0]) for ed in ("2024-2028", "2025-2029", "2026-2030")}
        self.assertEqual(counts, {"2024-2028": 24, "2025-2029": 17, "2026-2030": 12})

    def test_2026_list_conflicts_with_two_project_slides(self):
        recs, notes = parse_santee.parse("2026-2030")
        self.assertEqual(notes, [])
        conflicts = {r["name"]: r["isd"] for r in recs if any("the project's own slide" in i["msg"] for i in r["issues"])}
        self.assertEqual(conflicts, {"Reconductor Purrysburg - Mcintosh 230 kV tie lines": "2026-05-01", "Cross - Jefferies #2 230 kV Line": "2030-06-01"})


if __name__ == "__main__":
    unittest.main()
