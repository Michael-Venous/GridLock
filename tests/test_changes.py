"""Checks for pipeline/changes.py. Run: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import changes  # noqa: E402

SAV = {"lat": 32.08, "lon": -81.09}
OKATIE = {"lat": 32.30, "lon": -80.93}
COLUMBIA = {"lat": 34.00, "lon": -81.03}
PLANS = {"desc": {"reusesIds": True}, "ga": {"reusesIds": False}}


def rec(state, key, name, isd="2027-06-01", center=SAV, cost=None, plan=None):
    plan = plan or ("desc" if state == "SC" else "ga")
    return {"uid": f"{plan.upper()}:{key}:1", "key": key, "plan": plan, "state": state, "utility": "DESC" if state == "SC" else "GPC",
            "name": name, "project_id": f"TEAMS {key}" if plan == "ga" else key, "isd": isd, "center": center, "radiusMi": 0.5,
            "endpoints": [{"point": center}], "cost": {"total": cost}, "change": "", "source": {"doc": "x", "url": "u", "page": 1}}


class Matching(unittest.TestCase):
    def test_desc_needs_a_similar_name_but_a_lone_kept_id_is_a_rename(self):
        old = [rec("SC", "6809G", "Stevens Creek - Hooks 115kV"), rec("SC", "6809M", "St George - Sumter 230kV Tie")]
        new = [rec("SC", "6809G", "Hooks - Modoc 115/46 kV Rebuild"), rec("SC", "6809M", "St George - Sumter 230kV Tie"),
               rec("SC", "6809M", "Modoc - McCormick 115/46 kV Rebuild")]
        hows = sorted((n["name"] if n else o["name"], how) for o, n, how in changes.match(old, new, True))
        self.assertEqual(hows, [("Hooks - Modoc 115/46 kV Rebuild", "renamed"), ("Modoc - McCormick 115/46 kV Rebuild", "added"),
                                ("St George - Sumter 230kV Tie", "same")])

    def test_georgia_matches_on_teams_number_alone(self):
        old, new = [rec("GA", "20512", "KATHLEEN AREA IMPROVEMENTS")], [rec("GA", "20512", "BONAIRE PRIMARY - KATHLEEN 230kV LINE REBUILD")]
        self.assertEqual([how for _, _, how in changes.match(old, new, False)], ["same"])


class Events(unittest.TestCase):
    def setUp(self):
        self.filings = [
            {"id": "d1", "plan": "desc", "state": "SC", "utility": "DESC", "title": "D1", "date": "2024-03-01", "dateBasis": "x", "url": "u1"},
            {"id": "g1", "plan": "ga", "state": "GA", "utility": "Georgia ITS", "title": "G1", "date": "2025-01-31", "dateBasis": "x", "url": "u2"},
            {"id": "g2", "plan": "ga", "state": "GA", "utility": "Georgia ITS", "title": "G2", "date": "2026-02-27", "dateBasis": "x", "url": "u3"},
        ]
        self.editions = {
            "d1": [rec("SC", "6888", "Okatie - McIntosh Tie", isd="2028-12-31", center=OKATIE), rec("SC", "1", "Columbia Sub", center=COLUMBIA)],
            "g1": [rec("GA", "100", "KRAFT 230KV", isd="2027-06-01"), rec("GA", "200", "GOSHEN - KRAFT", isd="2028-06-01")],
            "g2": [rec("GA", "100", "KRAFT 230KV", isd="2029-06-01"), rec("GA", "300", "MCINTOSH RELAY", isd="2028-11-01")],
        }
        self.removed = {"g2": {"200": {"status": "cancelled", "zone": "219", "name": "GOSHEN - KRAFT", "last_need": "6/1/2028"}}}

    def test_first_edition_of_each_utility_is_a_baseline_not_an_event(self):
        evs = changes.events(self.filings, self.editions, self.removed, PLANS)
        self.assertEqual([e["id"] for e in evs], ["g2"])

    def test_projects_and_pairs(self):
        (ev,) = changes.events(self.filings, self.editions, self.removed, PLANS, {("ga", "GA:300:mcintosh relay"): "GA-300"})
        self.assertEqual(ev["plan"], "ga")
        by = {(c["kind"], c["key"]): c for c in ev["projects"]}
        self.assertEqual(by[("removed", "200")]["reason"], "Cancelled: listed in Table 3 of the new plan")
        self.assertEqual(by[("changed", "100")]["what"], ["date"])
        self.assertEqual(by[("changed", "100")]["days"], 731)
        self.assertIn(("added", "300"), by)
        self.assertEqual(by[("added", "300")]["appId"], "GA-300")
        self.assertIsNone(by[("changed", "100")].get("appId"))   # not in the current data passed in
        pairs = {(p["kind"], p["b"]["key"]): p for p in ev["pairs"]}
        self.assertTrue(all((p["a"]["plan"], p["b"]["plan"]) == ("desc", "ga") for p in ev["pairs"]))
        self.assertEqual(pairs[("new", "300")]["b"]["appId"], "GA-300")
        self.assertIsNone(pairs[("gone", "200")]["b"]["appId"])
        self.assertEqual(pairs[("new", "300")]["reason"], "TEAMS 300 is new")
        self.assertEqual(pairs[("gone", "200")]["reason"], "TEAMS 200 was dropped")
        self.assertEqual(pairs[("timing", "100")]["oldGapDays"], 579)
        self.assertEqual(pairs[("timing", "100")]["gapDays"], 152)
        self.assertEqual(ev["counts"]["pairsNew"], 1)
        self.assertNotIn("1", {p["a"]["key"] for p in ev["pairs"]})   # Columbia is over 25 mi from everything

    def test_a_second_south_carolina_utility_does_not_replace_desc(self):
        plans = {**PLANS, "santee": {"reusesIds": False}}
        filings = self.filings + [
            {"id": "s1", "plan": "santee", "state": "SC", "utility": "Santee Cooper", "title": "S1", "date": "2026-03-11", "dateBasis": "x", "url": "u4"},
            {"id": "d2", "plan": "desc", "state": "SC", "utility": "DESC", "title": "D2", "date": "2026-04-28", "dateBasis": "x", "url": "u5"},
        ]
        santee = rec("SC", "PURRYSBURG-IMPROVEMENTS", "Purrysburg Station Improvements", isd="2027-06-01", center=OKATIE, plan="santee")
        santee.update(uid="SCPSA:PURRYSBURG-IMPROVEMENTS", utility="SCPSA", project_id="Row 6")
        editions = dict(self.editions, s1=[santee], d2=self.editions["d1"])
        evs = {e["id"]: e for e in changes.events(filings, editions, self.removed, plans)}
        self.assertNotIn("s1", evs)             # Santee Cooper's first edition is its baseline
        self.assertEqual(evs["d2"]["counts"]["added"], 0)   # DESC compared with DESC, not with Santee Cooper
        self.assertEqual(evs["d2"]["counts"]["removed"], 0)
        self.assertNotIn(("desc", "santee"), {(p["a"]["plan"], p["b"]["plan"]) for p in evs["d2"]["pairs"]})
        active = {"desc": filings[-1], "santee": filings[-2], "ga": filings[2]}
        pair_plans = {frozenset((p["a"]["plan"], p["b"]["plan"])) for p in changes.all_pairs(active, editions, plans).values()}
        self.assertEqual(pair_plans, {frozenset(("desc", "ga")), frozenset(("santee", "ga"))})

    def test_pair_sides_carry_the_app_id(self):
        (ev,) = changes.events(self.filings, self.editions, self.removed, PLANS)
        pair = next(p for p in ev["pairs"] if p["kind"] == "new")
        self.assertIsNone(pair["a"]["appId"])
        self.assertIsNone(pair["b"]["appId"])

    def test_desc_drop_after_its_date_says_so(self):
        old = [rec("SC", "5", "Old Tap", isd="2025-04-30")]
        changes.assign_lineage([old], True)
        c = changes.project_changes(old, [], True, {"date": "2026-04-28"}, {})
        self.assertIn("was before the new list came out", c[0]["reason"])

    def test_a_filing_with_its_own_removed_table_is_taken_at_its_word(self):
        old = [rec("GA", "5", "OLD TAP", isd="2025-04-30"), rec("GA", "6", "OTHER TAP", isd="2025-04-30")]
        changes.assign_lineage([old], False)
        c = {x["key"]: x for x in changes.project_changes(old, [], False, {"date": "2026-02-27"}, {"6": {"status": "completed"}})}
        self.assertEqual(c["5"]["reason"], "No longer listed; the filing doesn't say why.")
        self.assertEqual(c["6"]["reason"], "Completed: listed in Table 4 of the new plan")

    def test_pair_rule_matches_the_app(self):
        # 17.848941051469826 is milesBetween() in src/match.js for the same two points
        self.assertAlmostEqual(changes.miles(SAV, OKATIE), 17.848941051469826, places=9)


class ThirdPlan(unittest.TestCase):
    """A plan the ingest agent adds pairs across states, never within a state or itself."""
    NEAR_SAV = {"lat": 32.10, "lon": -81.12}

    def setUp(self):
        # registered first, so it is side a against both DESC and Georgia even though "x" sorts after them
        self.plans = {"x": {"reusesIds": True}, **PLANS}
        self.filings = [
            {"id": "x1", "plan": "x", "state": "NC", "utility": "X", "title": "X1", "date": "2024-01-01", "dateBasis": "x", "url": "u0"},
            {"id": "d1", "plan": "desc", "state": "SC", "utility": "DESC", "title": "D1", "date": "2024-03-01", "dateBasis": "x", "url": "u1"},
            {"id": "g1", "plan": "ga", "state": "GA", "utility": "Georgia ITS", "title": "G1", "date": "2025-01-31", "dateBasis": "x", "url": "u2"},
            {"id": "x2", "plan": "x", "state": "NC", "utility": "X", "title": "X2", "date": "2026-01-01", "dateBasis": "x", "url": "u3"},
        ]
        x100 = lambda: rec("GA", "100", "ALPHA TIE", center=self.NEAR_SAV, plan="x")
        self.editions = {
            "x1": [x100()],
            "d1": [rec("SC", "6888", "Okatie - McIntosh Tie", isd="2028-12-31", center=OKATIE)],
            "g1": [rec("GA", "100", "ALPHA TIE", isd="2027-06-01")],   # same state, key and name as X's 100: another plan's project
            "x2": [x100(), rec("GA", "200", "BRAVO SUB", isd="2029-01-01", center=SAV, plan="x")],
        }

    def test_pairs_span_states_and_are_oriented_by_registry_order(self):
        fs = {f["plan"]: f for f in self.filings[1:]}
        for p in self.plans:
            changes.assign_lineage([self.editions[f["id"]] for f in self.filings if f["plan"] == p], self.plans[p]["reusesIds"])
        pairs = changes.all_pairs(fs, self.editions, self.plans)
        sides = sorted((p["a"]["plan"], p["a"]["key"], p["b"]["plan"], p["b"]["key"]) for p in pairs.values())
        self.assertEqual(sides, [("desc", "6888", "ga", "100"), ("x", "100", "desc", "6888"),
                                 ("x", "200", "desc", "6888")])   # X and GA are both GA, despite different plans

    def test_an_edition_of_the_third_plan_reports_its_cross_state_pairs(self):
        (ev,) = changes.events(self.filings, self.editions, {}, self.plans, {("x", "GA:200:bravo"): "X-200", ("ga", "GA:100:alpha tie"): "GA-100"})
        self.assertEqual((ev["id"], ev["plan"]), ("x2", "x"))
        self.assertEqual([(c["kind"], c["key"], c["plan"]) for c in ev["projects"]], [("added", "200", "x")])
        new = sorted((p["kind"], p["a"]["plan"], p["a"]["key"], p["b"]["plan"], p["b"]["key"]) for p in ev["pairs"])
        self.assertEqual(new, [("new", "x", "200", "desc", "6888")])
        self.assertTrue(all(p["a"]["plan"] != p["b"]["plan"] for p in ev["pairs"]))
        self.assertTrue(all(p["reason"] == "200 is new" and p["a"]["appId"] == "X-200" for p in ev["pairs"]))
        self.assertEqual({p["b"]["plan"]: p["b"]["appId"] for p in ev["pairs"]}, {"desc": None})
        self.assertEqual(ev["counts"]["pairsNew"], 1)


class RepeatedProjects(unittest.TestCase):
    """A filing that prints one key and name twice (an agent parser keys a project by its name when there is no printed ID)."""
    NEAR_SAV = {"lat": 32.10, "lon": -81.12}

    def jasper(self, n, isd):
        r = rec("SC", "T-2", "Jasper - Okatie 230 kV Line", isd=isd, center=self.NEAR_SAV, plan="x")
        r["uid"] = f"X:T-2:{n}"
        return r

    def setUp(self):
        self.plans = {"x": {"reusesIds": True}, **PLANS}
        self.filings = [
            {"id": "g1", "plan": "ga", "state": "GA", "utility": "Georgia ITS", "title": "G1", "date": "2025-01-31", "dateBasis": "x", "url": "u1"},
            {"id": "x1", "plan": "x", "state": "SC", "utility": "X", "title": "X1", "date": "2025-06-01", "dateBasis": "x", "url": "u2"},
            {"id": "x2", "plan": "x", "state": "SC", "utility": "X", "title": "X2", "date": "2026-06-01", "dateBasis": "x", "url": "u3"},
        ]
        self.editions = {
            "g1": [rec("GA", "100", "KRAFT 230KV", isd="2027-06-01")],
            "x1": [self.jasper(1, "2028-06-30"), self.jasper(2, "2029-06-30")],
            "x2": [self.jasper(1, "2028-06-30"), self.jasper(2, "2030-06-30")],   # only the second one slips
        }

    def test_each_repeat_keeps_its_own_pair(self):
        changes.assign_lineage([self.editions["g1"]], False)
        changes.assign_lineage([self.editions["x1"]], True)
        pairs = changes.all_pairs({"ga": self.filings[0], "x": self.filings[1]}, self.editions, self.plans)
        self.assertEqual(sorted((p["a"]["uid"], p["a"]["isd"], p["b"]["uid"]) for p in pairs.values()),
                         [("X:T-2:1", "2028-06-30", "GA:100:1"), ("X:T-2:2", "2029-06-30", "GA:100:1")])
        self.assertEqual([r["lineage"] for r in self.editions["x1"]], ["SC:T-2:jasper okatie line", "SC:T-2:jasper okatie line:2"])

    def test_repeats_carry_through_editions_in_print_order(self):
        (ev,) = changes.events(self.filings, self.editions, {}, self.plans)
        self.assertEqual([r["lineage"] for r in self.editions["x2"]], [r["lineage"] for r in self.editions["x1"]])
        self.assertEqual([(c["kind"], c["lineage"], c["oldIsd"], c["isd"]) for c in ev["projects"]],
                         [("changed", "SC:T-2:jasper okatie line:2", "2029-06-30", "2030-06-30")])
        self.assertEqual([(p["kind"], p["a"]["isd"], p["oldGapDays"], p["gapDays"]) for p in ev["pairs"]], [("timing", "2030-06-30", 760, 1125)])

    def test_a_new_project_never_takes_a_lineage_kept_from_the_edition_before(self):
        # matched on the ID alone, BRAVO keeps ALPHA's lineage; the new ALPHA printed after it takes the next ordinal
        old = [rec("GA", "7", "ALPHA TIE")]
        new = [rec("GA", "7", "BRAVO TIE"), rec("GA", "7", "ALPHA TIE")]
        changes.assign_lineage([old, new], False)
        self.assertEqual([r["lineage"] for r in new], ["GA:7:alpha tie", "GA:7:alpha tie:2"])


if __name__ == "__main__":
    unittest.main()
