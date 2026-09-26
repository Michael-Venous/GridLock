"""Checks for pipeline/changes.py. Run: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import changes  # noqa: E402

SAV = {"lat": 32.08, "lon": -81.09}
OKATIE = {"lat": 32.30, "lon": -80.93}
COLUMBIA = {"lat": 34.00, "lon": -81.03}


def rec(state, key, name, isd="2027-06-01", center=SAV, cost=None):
    return {"uid": f"{'DESC' if state == 'SC' else 'GA'}:{key}:1", "key": key, "state": state, "utility": "DESC" if state == "SC" else "GPC",
            "name": name, "project_id": key if state == "SC" else f"TEAMS {key}", "isd": isd, "center": center, "radiusMi": 0.5,
            "endpoints": [{"point": center}], "cost": {"total": cost}, "change": "", "source": {"doc": "x", "url": "u", "page": 1}}


class Matching(unittest.TestCase):
    def test_desc_needs_a_similar_name_but_a_lone_kept_id_is_a_rename(self):
        old = [rec("SC", "6809G", "Stevens Creek - Hooks 115kV"), rec("SC", "6809M", "St George - Sumter 230kV Tie")]
        new = [rec("SC", "6809G", "Hooks - Modoc 115/46 kV Rebuild"), rec("SC", "6809M", "St George - Sumter 230kV Tie"),
               rec("SC", "6809M", "Modoc - McCormick 115/46 kV Rebuild")]
        hows = sorted((n["name"] if n else o["name"], how) for o, n, how in changes.match(old, new, "SC"))
        self.assertEqual(hows, [("Hooks - Modoc 115/46 kV Rebuild", "renamed"), ("Modoc - McCormick 115/46 kV Rebuild", "added"),
                                ("St George - Sumter 230kV Tie", "same")])

    def test_georgia_matches_on_teams_number_alone(self):
        old, new = [rec("GA", "20512", "KATHLEEN AREA IMPROVEMENTS")], [rec("GA", "20512", "BONAIRE PRIMARY - KATHLEEN 230kV LINE REBUILD")]
        self.assertEqual([how for _, _, how in changes.match(old, new, "GA")], ["same"])


class Events(unittest.TestCase):
    def setUp(self):
        self.filings = [
            {"id": "d1", "state": "SC", "utility": "DESC", "title": "D1", "date": "2024-03-01", "dateBasis": "x", "url": "u1"},
            {"id": "g1", "state": "GA", "utility": "Georgia ITS", "title": "G1", "date": "2025-01-31", "dateBasis": "x", "url": "u2"},
            {"id": "g2", "state": "GA", "utility": "Georgia ITS", "title": "G2", "date": "2026-02-27", "dateBasis": "x", "url": "u3"},
        ]
        self.editions = {
            "d1": [rec("SC", "6888", "Okatie - McIntosh Tie", isd="2028-12-31", center=OKATIE), rec("SC", "1", "Columbia Sub", center=COLUMBIA)],
            "g1": [rec("GA", "100", "KRAFT 230KV", isd="2027-06-01"), rec("GA", "200", "GOSHEN - KRAFT", isd="2028-06-01")],
            "g2": [rec("GA", "100", "KRAFT 230KV", isd="2029-06-01"), rec("GA", "300", "MCINTOSH RELAY", isd="2028-11-01")],
        }
        self.removed = {"g2": {"200": {"status": "cancelled", "zone": "219", "name": "GOSHEN - KRAFT", "last_need": "6/1/2028"}}}

    def test_first_edition_of_each_utility_is_a_baseline_not_an_event(self):
        evs = changes.events(self.filings, self.editions, self.removed)
        self.assertEqual([e["id"] for e in evs], ["g2"])

    def test_projects_and_pairs(self):
        (ev,) = changes.events(self.filings, self.editions, self.removed)
        by = {(c["kind"], c["key"]): c for c in ev["projects"]}
        self.assertEqual(by[("removed", "200")]["reason"], "Cancelled: listed in Table 3 of the new plan")
        self.assertEqual(by[("changed", "100")]["what"], ["date"])
        self.assertEqual(by[("changed", "100")]["days"], 731)
        self.assertIn(("added", "300"), by)
        pairs = {(p["kind"], p["ga"]["key"]): p for p in ev["pairs"]}
        self.assertEqual(pairs[("new", "300")]["reason"], "TEAMS 300 is new")
        self.assertEqual(pairs[("gone", "200")]["reason"], "TEAMS 200 was dropped")
        self.assertEqual(pairs[("timing", "100")]["oldGapDays"], 579)
        self.assertEqual(pairs[("timing", "100")]["gapDays"], 152)
        self.assertEqual(ev["counts"]["pairsNew"], 1)
        self.assertNotIn("1", {p["desc"]["key"] for p in ev["pairs"]})   # Columbia is over 25 mi from everything

    def test_desc_drop_after_its_date_says_so(self):
        old = [rec("SC", "5", "Old Tap", isd="2025-04-30")]
        changes.assign_lineage([old], "SC")
        c = changes.project_changes(old, [], "SC", {"date": "2026-04-28"}, {})
        self.assertIn("was before the new list came out", c[0]["reason"])

    def test_pair_rule_matches_the_app(self):
        # 17.848941051469826 is milesBetween() in src/match.js for the same two points
        self.assertAlmostEqual(changes.miles(SAV, OKATIE), 17.848941051469826, places=9)


if __name__ == "__main__":
    unittest.main()
