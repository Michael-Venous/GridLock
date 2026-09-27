"""The checks an agent-written parser's output must pass (pipeline/generated_parser.py) and the sandbox it runs in."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import generated_parser as gp  # noqa: E402
import sandbox  # noqa: E402
from common import pdf_pages  # noqa: E402

# a table with its unit in the header, one row per project
TABLE = ["Acme Power Company\nPlanned Transmission Projects ($000)\n\n"
         "ID       Project                         In Service    Cost\n"
         "A-101    Foo 115 kV Line Rebuild         6/1/2027      4,200\n"
         "A-102    Bar Substation Expansion        12/1/2029     18,900\n"
         "A-103    Baz Tap                         6/1/2028      1,250\n"]
ROWS = [{"project_id": "A-101", "name": "Foo 115 kV Line Rebuild", "page": 1, "in_service": "6/1/2027", "cost_raw": "4,200", "cost_total": 4200000},
        {"project_id": "A-102", "name": "Bar Substation Expansion", "page": 1, "in_service": "12/1/2029", "cost_raw": "18,900", "cost_total": 18900000},
        {"project_id": "A-103", "name": "Baz Tap", "page": 1, "in_service": "6/1/2028", "cost_raw": "1,250", "cost_total": 1250000}]
# one project per page, after a cover page
DETAIL = ["Acme Power Company\nPlanned Transmission Projects\n"] + [
    f"Project {n} of 3\n\n{name}\nProject ID: {pid}\nIn-Service Date: {isd}\nEstimated Cost: {cost}\nDescription: {desc}\n"
    for n, (name, pid, isd, cost, desc) in enumerate([
        ("Foo 115 kV Line Rebuild", "A-101", "6/1/2027", "$4,200,000", "Rebuild 2.5 miles of the Foo - Bar 115 kV line."),
        ("Bar Substation Expansion", "A-102", "12/1/2029", "$18,900,000", "Expand the Bar substation."),
        ("Baz Tap", "A-103", "6/1/2028", "$1,250,000", "New tap to Baz.")], 1)]
PAGED = [{"name": "Foo 115 kV Line Rebuild", "page": 2, "project_id": "A-101", "in_service": "6/1/2027", "cost_raw": "$4,200,000",
          "cost_total": 4200000, "description": "Rebuild 2.5 miles of the Foo - Bar 115 kV line."},
         {"name": "Bar Substation Expansion", "page": 3, "project_id": "A-102", "in_service": "12/1/2029", "cost_raw": "$18,900,000",
          "cost_total": 18900000, "description": "Expand the Bar substation."},
         {"name": "Baz Tap", "page": 4, "project_id": "A-103", "in_service": "6/1/2028", "cost_raw": "$1,250,000", "cost_total": 1250000,
          "description": "New tap to Baz."}]


def problems(recs, pages):
    return " | ".join(gp.check(recs, pages)[0])


def returning(recs):
    """Parser code that returns these records."""
    return f"import json\ndef parse(pages):\n    return json.loads({json.dumps(recs)!r})\n"


class GroundingTests(unittest.TestCase):
    def test_faithful_records_pass(self):
        self.assertEqual(gp.check(ROWS, TABLE)[0], [])
        self.assertEqual(gp.check(PAGED, DETAIL)[0], [])

    def test_a_neighbours_values_fail_in_a_table_and_across_pages(self):
        swapped = [dict(ROWS[0], in_service="12/1/2029"), dict(ROWS[1], in_service="6/1/2027"), ROWS[2]]
        self.assertIn("in_service '12/1/2029' is not printed as one phrase in its entry", problems(swapped, TABLE))
        # every record given the next page's values: they are on the page after, which belongs to the next entry
        shifted = [dict(PAGED[i], **{k: PAGED[i + 1][k] for k in ("in_service", "cost_raw", "cost_total", "description")}) for i in range(2)]
        found = gp.check(shifted + PAGED[2:], DETAIL)[0]
        self.assertEqual(sorted({p.split(":")[0] for p in found}), ["record 0 ('Foo 115 kV Line Rebuild', page 2)",
                                                                     "record 1 ('Bar Substation Expansion', page 3)"])

    def test_the_name_or_id_must_be_on_the_records_page(self):
        early = [dict(r, page=r["page"] - 1) for r in PAGED]
        self.assertEqual(problems(early, DETAIL).count("neither its name nor its project_id is printed on page"), 3)

    def test_values_are_phrases_not_bags_of_words(self):
        # '$4,200' is not part of '$4,200,000', and '1/12/2027' is not '12/1/2029' or '6/1/2027'
        self.assertIn("cost_raw '$4,200'", problems([dict(PAGED[0], cost_raw="$4,200", cost_total=4200)], DETAIL))
        self.assertIn("in_service '1/12/2027'", problems([dict(PAGED[0], in_service="1/12/2027")], DETAIL))
        self.assertIn("name 'Rebuild Foo 115 kV Line' is not printed as one phrase", problems([dict(PAGED[0], name="Rebuild Foo 115 kV Line")], DETAIL))

    def test_a_wrapped_table_cell_is_one_phrase(self):
        pages = ["ID      Project                  In Service\n"
                 "        ADAMSVILLE - BUZZARD\n"
                 "19597   ROOST 230kV REBUILD      6/1/2026\n"
                 "        AND JUMPER UPGRADE\n"]
        rec = {"project_id": "19597", "name": "ADAMSVILLE - BUZZARD ROOST 230kV REBUILD AND JUMPER UPGRADE", "page": 1, "in_service": "6/1/2026"}
        self.assertEqual(gp.check([rec], pages)[0], [])

    def test_a_detail_page_may_title_the_project_a_little_differently_than_its_table_row(self):
        pages = ["Project Title                                 In-service Date\n"
                 "Yemassee - Varnville 230 kV line Rebuild      12/1/2029\n"
                 "Marion - Red Bluff 230 kV line                12/1/2029\n",
                 "Yemassee-Varnville 230 kV Rebuild\n\nProject Status\nApproved\n", "Marion Red Bluff Line\n\nProject Status\nApproved\n"]
        rec = {"name": "Yemassee - Varnville 230 kV line Rebuild", "page": 2, "in_service": "12/1/2029", "status": "Approved"}
        self.assertEqual(gp.check([rec], pages)[0], [])
        # a number may not differ, nor more than one word in five
        self.assertIn("neither its name", problems([dict(rec, name="Yemassee - Varnville 115 kV line Rebuild")], pages))
        self.assertIn("neither its name", problems([dict(rec, name="Marion - Red Bluff 230 kV line", page=3)], pages))

    def test_prose_may_lose_a_hyphenated_word_but_not_a_number(self):
        self.assertIn("['25']", problems([dict(PAGED[0], description="Rebuild 25 miles of the Foo - Bar 115 kV line.")], DETAIL))
        self.assertEqual(problems([dict(PAGED[0], description="Rebuild 2.5 miles of the Foo - Bar 115 kV transmission line.")], DETAIL), "")


class RowTests(unittest.TestCase):
    SUMMARY = ("ID         Zone   Sponsor   Notes\n"
               "21319      215    GPC\n"
               "21400      231    DALTON    replaces 21319 segment\n"
               "5392 A-C   208    SAV\n\n"
               "Changes since the last plan: 21319 in-service moved from 6/1/2025 to 12/1/2029; cost was $5,500,000.\n")
    PAGES = [SUMMARY, "Foo Line\nProject ID: 21319\nIn service: 12/1/2029\n", "Bar Line\nProject ID: 21400\nIn service: 6/1/2030\n",
             "Baz Sub\nProject ID: 5392 A\nIn service: 2031\n", "Qux Sub\nProject ID: 5392 A-C\nIn service: 2032\n"]
    RECS = [{"name": "Foo Line", "project_id": "21319", "page": 2, "in_service": "12/1/2029", "zone": "215", "sponsor": "GPC"},
            {"name": "Bar Line", "project_id": "21400", "page": 3, "in_service": "6/1/2030", "zone": "231", "sponsor": "DALTON"},
            {"name": "Baz Sub", "project_id": "5392 A", "page": 4, "in_service": "2031"},
            {"name": "Qux Sub", "project_id": "5392 A-C", "page": 5, "in_service": "2032", "zone": "208", "sponsor": "SAV"}]

    def test_whole_cells_join_from_the_row_the_id_starts(self):
        self.assertEqual(gp.check(self.RECS, self.PAGES)[0], [])

    def test_a_row_that_only_mentions_the_id_is_another_projects(self):
        found = problems([dict(self.RECS[0], zone="231", sponsor="DALTON")] + self.RECS[1:], self.PAGES)
        self.assertIn("zone '231'", found)
        self.assertIn("sponsor 'DALTON'", found)
        # '5392 A' is not the start of '5392 A-C'
        self.assertIn("zone '208'", problems(self.RECS[:2] + [dict(self.RECS[2], zone="208")] + self.RECS[3:], self.PAGES))

    def test_prose_mentioning_the_id_is_not_a_row(self):
        self.assertIn("in_service '6/1/2025'", problems([dict(self.RECS[0], in_service="6/1/2025")] + self.RECS[1:], self.PAGES))


class CostTests(unittest.TestCase):
    def test_amounts_take_the_unit_the_page_states(self):
        self.assertIn("page 1 states amounts in thousands", problems([dict(ROWS[0], cost_total=4200)], TABLE))
        self.assertIn("page 1 states amounts in thousands", problems([dict(ROWS[0], cost_total=4.2e9)], TABLE))
        # no unit stated: the amount is dollars, and a unit can't be supposed
        self.assertIn("no unit", problems([dict(PAGED[0], cost_total=4.2e9)], DETAIL))
        # a unit on the amount itself is not applied twice
        pages = ["Project A-1  Foo Line\n  In service: 2027\n  Cost: $12.5M\n"]
        rec = {"name": "Foo Line", "project_id": "A-1", "page": 1, "in_service": "2027", "cost_raw": "$12.5M"}
        self.assertEqual(problems([dict(rec, cost_total=12.5e6)], pages), "")
        self.assertIn("carries its own unit", problems([dict(rec, cost_total=12.5e9)], pages))

    def test_yearly_amounts_are_printed_in_the_entry(self):
        pages = ["Project A-1  Foo Line\n  In service: 6/1/2027\n  Previous   2026         2027         2028   Total\n"
                 "  $0         $1,000,000   $3,200,000   $0     $4,200,000\n"]
        rec = {"name": "Foo Line", "project_id": "A-1", "page": 1, "in_service": "6/1/2027", "cost_raw": "$4,200,000", "cost_total": 4200000}
        self.assertEqual(gp.check([dict(rec, cost_by_year={"2026": 1000000, "2027": 3200000, "2028": 0})], pages), ([], [[]]))
        found = problems([dict(rec, cost_by_year={"Prior": 1, "2019": 1, "2027": 3200000})], pages)
        self.assertIn("year 'Prior' is not printed", found)
        self.assertIn("year '2019' is not printed", found)
        self.assertIn("cost_by_year '2026': 99 is not an amount printed", problems([dict(rec, cost_by_year={"2026": 99})], pages))
        self.assertIn("is inf, not a number", problems([dict(rec, cost_by_year={"2026": float("inf")})], pages))
        # a row that doesn't add up is the filing's own inconsistency, kept as a note
        _, notes = gp.check([dict(rec, cost_by_year={"2027": 3200000})], pages)
        self.assertIn("add up to $3,200,000, but the total printed is $4,200,000", notes[0][0]["msg"])


class RecordTests(unittest.TestCase):
    def test_projects_no_longer_planned_are_left_out(self):
        self.assertIn("status 'Cancelled'", problems([dict(PAGED[0], status="Cancelled")], [p.replace("Project ID", "Status: Cancelled\nProject ID") for p in DETAIL]))
        pages = [DETAIL[0], "Table 3 Cancelled Projects\n\n" + DETAIL[1]]
        self.assertIn("listed under 'Table 3 Cancelled Projects'", problems([PAGED[0]], pages))
        self.assertEqual(problems([dict(PAGED[0], status="Complete by 2027")], [p.replace("Project ID", "Status: Complete by 2027\nProject ID") for p in DETAIL]), "")

    def test_a_state_is_printed_as_a_code_or_its_name(self):
        pages = ["Project A-1  Foo Line\nStatus: IN SERVICE soon\nNorth Charleston, South Carolina\nIn service: 2027\n"]
        rec = {"name": "Foo Line", "project_id": "A-1", "page": 1, "in_service": "2027"}
        self.assertIn("state 'IN'", problems([dict(rec, state="IN")], pages))
        self.assertIn("state 'NC'", problems([dict(rec, state="NC")], pages))
        self.assertEqual(problems([dict(rec, state="SC")], pages), "")

    def test_a_project_is_listed_once_and_named(self):
        pages = DETAIL[:3] + ["Bar Substation Expansion (continued)\nProject ID: A-102\n"]
        self.assertIn("the same project as record 1", problems(PAGED[:2] + [dict(PAGED[1], page=4, in_service=None, cost_raw=None,
                                                                                            cost_total=None, description=None)], pages))
        self.assertIn("name '-' has no words", problems([dict(PAGED[0], name="-")], DETAIL))
        self.assertIn("name '   ' has no words", problems([dict(PAGED[0], name="   ")], DETAIL))

    def test_an_undated_record_whose_entry_prints_a_date_is_a_parser_mistake(self):
        self.assertIn("its entry prints one: 'In-Service Date: 6/1/2027'", problems([dict(PAGED[0], in_service=None)], DETAIL))
        pages = [DETAIL[0], DETAIL[1].replace("In-Service Date: 6/1/2027\n", "")]
        found, notes = gp.check([dict(PAGED[0], in_service=None)], pages)
        self.assertEqual((found, notes[0][0]["msg"]), ([], "the filing gives no in-service date for this project"))


class EvaluateTests(unittest.TestCase):
    def test_most_records_need_a_date(self):
        undated = [dict(r, in_service=None) for r in PAGED]
        pages = [p.replace("In-Service Date", "Target") for p in DETAIL]
        rep, _, _ = gp.evaluate(returning([PAGED[0] | {"in_service": None}] + undated[1:]), pages, r"Project \d+ of \d+")
        self.assertIn("no record has a readable in-service date", " ".join(rep["blocking"]))
        rep, _, _ = gp.evaluate(returning([dict(PAGED[0], in_service="6/1/2027")] + undated[1:]),
                                [DETAIL[0], DETAIL[1]] + pages[2:], r"Project \d+ of \d+")
        self.assertIn("only 1 of 3 records have a readable in-service date", " ".join(rep["blocking"]))

    def test_a_later_edition_may_not_lose_most_records_or_dates(self):
        code = returning(PAGED)
        self.assertTrue(gp.evaluate(code, DETAIL, r"Project \d+ of \d+", registered={"records": 4, "withInServiceDate": 4})[0]["accepted"])
        rep, _, _ = gp.evaluate(code, DETAIL, r"Project \d+ of \d+", registered={"records": 10, "withInServiceDate": 10})
        self.assertIn("3 records, where the edition this parser was checked on had 10", " ".join(rep["blocking"]))

    def test_only_newer_editions_are_held_to_the_checked_numbers(self):
        parser = {"writtenFor": "acme-2027", "checked": {"records": 3, "withInServiceDate": 3}}
        real = gp.filings
        gp.filings = lambda: [{"id": "acme-2026", "date": "2026-01-01"}, {"id": "acme-2027", "date": "2027-01-01"}, {"id": "acme-2028", "date": "2028-01-01"}]
        try:
            self.assertIsNone(gp.checked_against({"id": "acme-2026", "date": "2026-01-01"}, parser))
            self.assertIsNone(gp.checked_against({"id": "acme-2027", "date": "2027-01-01"}, parser))
            self.assertEqual(gp.checked_against({"id": "acme-2028", "date": "2028-01-01"}, parser), parser["checked"])
        finally:
            gp.filings = real

    def test_the_count_marker_must_be_each_projects_own(self):
        code = returning(PAGED)
        self.assertTrue(gp.evaluate(code, DETAIL, r"Project \d+ of \d+")[0]["accepted"])
        # a field label counts only the projects that print that field
        rep, _, _ = gp.evaluate(code, DETAIL, r"In-Service Date")
        self.assertIn("count_pattern matches a field label", " ".join(rep["blocking"]))
        # the right number of matches, but one is on the cover and one project has none
        pages = [DETAIL[0] + "Project 9 of 9 was withdrawn\n"] + DETAIL[1:]
        rep, _, _ = gp.evaluate(code, pages, r"Project [1-29] of \d")
        self.assertEqual(rep["count_pattern_matches"], 3)
        self.assertIn("record 2 ('Baz Tap', page 4) has no count_pattern match", " ".join(rep["blocking"]))

    def test_a_scope_counts_one_printing_of_a_repeated_table(self):
        pages = [TABLE[0].replace("Planned Transmission Projects ($000)", "Committed Facilities ($000)"), TABLE[0]]
        recs = [dict(r, page=2) for r in ROWS]
        rep, _, _ = gp.evaluate(returning(recs), pages, r"(?m)^A-\d+")
        self.assertIn("count_pattern matches 6 times", " ".join(rep["blocking"]))
        rep, _, _ = gp.evaluate(returning(recs), pages, r"(?m)^A-\d+", r"Planned Transmission Projects")
        self.assertTrue(rep["accepted"], rep["blocking"])

    def test_a_count_pattern_that_backtracks_is_stopped(self):
        real = sandbox.FIND_TIMEOUT_S
        sandbox.FIND_TIMEOUT_S = 2
        try:
            t = time.time()
            rep, _, _ = gp.evaluate(returning(PAGED), [p + "Project Description Description Description Description Description\n" for p in DETAIL],
                                    r"Project (\w+\s?)+ of \d+")
        finally:
            sandbox.FIND_TIMEOUT_S = real
        self.assertLess(time.time() - t, 20)
        self.assertIn("ran longer than 2 s", " ".join(rep["blocking"]))


class NormalizeTests(unittest.TestCase):
    FILING = {"plan": "acme", "title": "Acme", "url": "https://example.org/a.pdf"}
    PLAN = {"name": "Acme", "owner": "Acme Power", "state": "SC"}

    def test_a_start_after_in_service_falls_back_to_the_budget(self):
        rec = {"name": "Foo", "page": 1, "in_service": "6/1/2027", "start": "2028", "cost_by_year": {"2026": 5, "2027": 5}}
        out = gp.normalize([rec], [[]], self.FILING, {}, self.PLAN)[0]
        self.assertEqual(out["window"], {"start": "2026-01-01", "end": "2027-06-01", "basis": "first budget year with spend → in-service date"})
        self.assertIn("start 2028-01-01 is after in-service date 2027-06-01", out["issues"][0]["msg"])
        out = gp.normalize([dict(rec, cost_by_year={})], [[]], self.FILING, {}, self.PLAN)[0]
        self.assertEqual(out["window"]["basis"], "the filing's start falls after its in-service date")

    def test_footnote_marks_are_not_part_of_a_key(self):
        self.assertEqual([gp.key_of({"project_id": p}) for p in ("A-101*", "A-101", "(A-101)", "A 101¹", "a-101†")],
                         ["A-101", "A-101", "A-101", "A101", "A-101"])


class SandboxLimitTests(unittest.TestCase):
    def test_output_is_capped_while_it_is_written(self):
        for code in ('def parse(pages):\n    s = "A" * (1 << 20)\n    for _ in range(200): print(s)\n    return []',
                     'def parse(pages):\n    return ["A" * (1 << 20)] * 40'):
            t = time.time()
            recs, err = sandbox.run(code, ["x"])
            self.assertIsNone(recs)
            self.assertIn("larger than 32 MB", err)
            self.assertLess(time.time() - t, 30)

    def test_printing_does_not_corrupt_the_records(self):
        self.assertEqual(sandbox.run('def parse(pages):\n    print("debug")\n    return [1]', ["x"]), ([1], None))

    def test_numbers_json_does_not_allow_are_refused(self):
        self.assertIn("NaN", sandbox.run("import math\ndef parse(pages): return [math.nan]", ["x"])[1])

    def test_no_os_isolation_means_no_run_unless_allowed(self):
        real, env = sandbox._ISO, os.environ.pop(sandbox.UNISOLATED, None)
        sandbox._ISO = ("none", [sys.executable])
        try:
            recs, err = sandbox.run("def parse(pages): return [1]", ["x"])
            self.assertIsNone(recs)
            self.assertIn(sandbox.UNISOLATED, err)
            os.environ[sandbox.UNISOLATED] = "1"
            self.assertEqual(sandbox.run("def parse(pages): return [1]", ["x"]), ([1], None))
        finally:
            sandbox._ISO = real
            os.environ.pop(sandbox.UNISOLATED, None)
            if env is not None:
                os.environ[sandbox.UNISOLATED] = env


@unittest.skipUnless((ROOT / "data/build/ingest/e98bfeb86f22/parser.py").exists(), "needs an agent-written DESC parser in data/build/ingest")
class RealFilingTests(unittest.TestCase):
    """A parser the agent wrote for DESC 2026-2030 passes; the same output, off by one project, does not."""

    def test_a_shifted_reading_of_a_real_filing_fails(self):
        work = ROOT / "data/build/ingest/e98bfeb86f22"
        pages = pdf_pages(work / "source.pdf")
        rep, raw, _ = gp.evaluate((work / "parser.py").read_text(), pages, r"Project \d+ of \d+")
        self.assertTrue(rep["accepted"], rep.get("blocking") or rep.get("error"))
        keys = ("in_service", "cost_raw", "cost_total", "cost_by_year", "description", "need", "status")
        shifted = [dict(r, **{k: n[k] for k in keys if k in n}) for r, n in zip(raw, raw[1:])] + raw[-1:]
        differs = {i for i, (r, s) in enumerate(zip(raw, shifted)) if r != s}
        found = {int(p.split()[1]) for p in gp.check(shifted, pages)[0]}
        self.assertEqual(differs - found, set())
        self.assertEqual(len({int(p.split()[1]) for p in gp.check([dict(r, page=r["page"] - 1) for r in raw], pages)[0]}), len(raw))


if __name__ == "__main__":
    unittest.main()
