"""Offline regression cases for identity and geographic evidence safeguards.

Run: python3 -m unittest discover -s tests -p 'test_pipeline.py'
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import build
import geocode
import parse_ga
from common import norm_name


def record(name="Test", uid="GA:99999", state="GA", plan=None):
    return {"name": name, "uid": uid, "state": state, "plan": plan or uid.split(":")[0].lower(), "key": uid.split(":")[1], "issues": [], "miles": 10}


def endpoint(name="A", method="osm-exact", confidence="high", point=True):
    return {"name": name, "point": {"lat": 33, "lon": -84} if point else None,
            "method": method if point else None, "confidence": confidence if point else "none",
            "radiusMi": 0.5 if point else None, "evidence": "test"}


class StationExtractionTests(unittest.TestCase):
    def test_names_that_start_with_descriptors_survive(self):
        for title, expected in (
            ("LINE CREEK 115kV BREAKER REPLACEMENTS", ["LINE CREEK"]),
            ("GTC: NEW HAMPTON 230/115kV SUB", ["NEW HAMPTON"]),
            ("GTC: YATES - LINE CREEK 230kV REBUILD", ["YATES", "LINE CREEK"]),
            ("PROJECT SPEEDWAY 230kV SUBSTATION", ["PROJECT SPEEDWAY"]),
            ("GTC: SWITCH WAY - THORNTON ROAD 230kV LINE REBUILD", ["SWITCH WAY", "THORNTON ROAD"]),
        ):
            with self.subTest(title=title):
                self.assertEqual(geocode.endpoint_names(record(title)), expected)

    def test_action_prefixes_colons_ampersands_and_stop_words(self):
        for title, expected in (
            ("MORROW: REMOVE LIMITING ELEMENTS ON AUTO TRANSFORMER A", ["MORROW"]),
            ("LLOYD SHOALS: REMOVE LIMITING ELEMENTS ON 115kV", ["LLOYD SHOALS"]),
            ("CC: NORTH SPA 230kV (NETWORK IMPROVEMENTS)", ["NORTH SPA"]),
            ("SMART VALVES AT EAST VILLA RICA SWITCHING STATION", ["EAST VILLA RICA"]),
            ("OLIVER DAM & BULL CREEK 115kV PROTECTION RELAY UPGRADE", ["OLIVER DAM", "BULL CREEK"]),
            ("GORDON OVERSTRESSED 115kV BREAKERS REPLACEMENT", ["GORDON"]),
            ("CC - HILL VIEW & GRASSY HOLLOW SUB - IMPROVEMENTS", ["HILL VIEW", "GRASSY HOLLOW"]),
        ):
            with self.subTest(title=title):
                self.assertEqual(geocode.endpoint_names(record(title)), expected)

    def test_normalization_preserves_equivalent_station_names(self):
        self.assertEqual(norm_name("O'Hara Substation"), norm_name("OHARA"))
        self.assertEqual(norm_name("Saluda Hydroelectric Plant"), norm_name("Saluda Hydro"))

    def test_a_parser_can_name_the_stations_and_overrides_still_win(self):
        rec = record("Reconductor Purrysburg - Mcintosh 230 kV tie lines", uid="SCPSA:RECONDUCTOR-PURRYSBURG-MCINTOSH-TIE-LINES", state="SC")
        rec["endpoint_names"] = ["Purrysburg", "McIntosh"]
        self.assertEqual(geocode.endpoint_names(rec), ["Purrysburg", "McIntosh"])
        ov = {"endpoints": {"SCPSA:RECONDUCTOR-PURRYSBURG-MCINTOSH-TIE-LINES": ["McIntosh"]}, "points": {}}
        with patch.object(geocode, "OVERRIDES", ov):
            self.assertEqual(geocode.endpoint_names(rec), ["McIntosh"])

    def test_georgia_title_conventions_belong_to_its_plan(self):
        self.assertEqual(geocode.endpoint_names(record("GTC: YATES - LINE CREEK 230kV REBUILD")), ["YATES", "LINE CREEK"])
        # another plan's GA project keeps its own title: "SAV" there is not a Georgia ITS sponsor tag
        self.assertEqual(geocode.endpoint_names(record("SAV - OGEECHEE 115kV LINE", uid="X:1", plan="x")), ["SAV", "OGEECHEE"])

    def test_fuzzy_matching_cannot_swap_directions(self):
        osm = geocode.OSMIndex.__new__(geocode.OSMIndex)
        osm.items = [{"norm": "west villa rica", "name": "West Villa Rica Substation", "lat": 33, "lon": -84}]
        self.assertEqual(osm.find("East Villa Rica", "GA"), ([], None))
        self.assertFalse(geocode.compatible_directions("north griffin", "south griffin"))


class GeocodingEvidenceTests(unittest.TestCase):
    def test_nominatim_evidence_describes_selected_result(self):
        candidates = [{"lat": 33.5, "lon": -84, "label": "place/village: First town"},
                      {"lat": 32, "lon": -81, "label": "highway/residential: Selected road"}]
        osm = Mock()
        osm.find.return_value = ([], None)
        with patch.object(geocode, "nominatim", return_value=candidates):
            eps = geocode.geocode_project(record("Example"), osm, {}, anchor={"lat": 32, "lon": -81}, allow_network=False)
        self.assertEqual(eps[0]["point"], {"lat": 32, "lon": -81})
        self.assertIn("Selected road", eps[0]["evidence"])
        self.assertNotIn("First town", eps[0]["evidence"])

    def test_manual_point_can_retain_lower_confidence_and_wider_radius(self):
        ov = {"endpoints": {}, "points": {"GA:example": {"lat": 33, "lon": -84, "why": "Public corroboration, unconfirmed", "confidence": "medium", "radiusMi": 2}}}
        with patch.object(geocode, "OVERRIDES", ov):
            eps = geocode.geocode_project(record("Example"), Mock(), {}, allow_network=False)
        self.assertEqual((eps[0]["confidence"], eps[0]["radiusMi"]), ("medium", 2))

    def test_a_station_of_the_other_south_carolina_utility_is_kept_at_medium_confidence(self):
        osm = osm_items(("Bluffton Substation", 32.235, -80.8534))
        osm.items[0]["operator"] = "South Carolina Electric & Gas"
        rec = record("Bluffton Station Improvements", uid="SCPSA:BLUFFTON-IMPROVEMENTS", state="SC")
        rec.update(utility="SCPSA", endpoint_names=["Bluffton"])
        (ep,) = geocode.geocode_project(rec, osm, {}, allow_network=False)
        self.assertEqual((ep["method"], ep["confidence"], ep["radiusMi"]), ("osm-exact", "medium", 1.5))
        self.assertIn("another utility's station", ep["evidence"])
        rec.update(utility="DESC", uid="DESC:1:1")   # DESC's own station stays high
        (ep,) = geocode.geocode_project(rec, osm, {}, allow_network=False)
        self.assertEqual(ep["confidence"], "high")
        refs = {"SC:bluffton": {"lat": 32.235, "lon": -80.8534, "utility": "DESC"}}   # the sponsor's workbook: DESC stations
        rec.update(utility="SCPSA", uid="SCPSA:BLUFFTON-IMPROVEMENTS")
        (ep,) = geocode.geocode_project(rec, osm, refs, allow_network=False)
        self.assertEqual((ep["method"], ep["confidence"]), ("reference", "medium"))
        self.assertIn("DESC's station of this name", ep["evidence"])

    def test_a_tie_to_a_georgia_plant_is_not_an_owner_mismatch(self):
        rec = {"state": "SC", "utility": "SCPSA"}
        self.assertIsNone(geocode.other_sc_owner(rec, {"operator": "Georgia Power"}))
        self.assertIsNone(geocode.other_sc_owner(rec, {"operator": ""}))
        self.assertEqual(geocode.other_sc_owner(rec, {"operator": "Duke Energy Progress"}), "Duke Energy Progress")

    def test_invalid_manual_uncertainty_is_rejected(self):
        ov = {"endpoints": {}, "points": {"GA:example": {"lat": 33, "lon": -84, "why": "test", "confidence": "certain", "radiusMi": -1}}}
        with patch.object(geocode, "OVERRIDES", ov), self.assertRaises(ValueError):
            geocode.geocode_project(record("Example"), Mock(), {}, allow_network=False)


def osm_items(*rows):
    osm = geocode.OSMIndex.__new__(geocode.OSMIndex)
    osm.items = [{"norm": norm_name(n), "name": n, "lat": lat, "lon": lon, "osm": f"way/{i}", "power": "substation", "operator": ""}
                 for i, (n, lat, lon) in enumerate(rows)]
    return osm


class PlaceNameTests(unittest.TestCase):
    def result(self, name, cls="place"):
        return {"name": name, "display_name": f"{name}, Georgia", "class": cls}

    def test_a_stand_in_place_must_carry_the_station_name(self):
        self.assertTrue(geocode.names_place(self.result("Hampton", "boundary"), "HAMPTON"))
        self.assertTrue(geocode.names_place(self.result("Garrett Road", "highway"), "GARRETT RD"))
        self.assertTrue(geocode.names_place(self.result("Harry Truman Parkway", "highway"), "TRUMAN PARKWAY"))
        self.assertTrue(geocode.names_place(self.result("Talbot County", "boundary"), "TALBOT CO"))
        self.assertFalse(geocode.names_place(self.result("Alvin Griffin Irrigation Pond Dam South", "waterway"), "SOUTH GRIFFIN"))
        self.assertFalse(geocode.names_place(self.result("Jones Bridge Hills", "landuse"), "HILLS BRIDGE"))
        self.assertFalse(geocode.names_place(self.result("Dublin", "boundary"), "N DUBLIN"))

    def test_institutions_peaks_and_regions_are_not_stand_ins(self):
        self.assertFalse(geocode.names_place(self.result("Tomochichi", "amenity"), "TOMOCHICHI"))
        self.assertFalse(geocode.names_place(self.result("Buzzard Roost", "natural"), "BUZZARD ROOST"))
        self.assertFalse(geocode.names_place(self.result("North Georgia Avenue", "highway"), "NORTH GEORGIA"))


class DescribedStationTests(unittest.TestCase):
    def test_description_names_existing_stations_by_whole_name(self):
        osm = osm_items(("Bonaire Primary Substation", 32.55, -83.6), ("Scherer", 33.06, -83.8), ("Griffin", 33.25, -84.26))
        rec = record()
        rec["description"] = "Build a 500/230kV station splitting the Bonaire Primary - Scherer 500kV line near Big South Griffin."
        found = geocode.described_stations(rec, osm)
        self.assertEqual([d["name"] for d in found], ["Bonaire Primary", "Scherer"])
        self.assertIn("Named in the filing description", found[0]["evidence"])
        self.assertIn("Bonaire Primary - Scherer", found[0]["evidence"])

    def test_a_name_needs_one_place_within_the_zone(self):
        osm = osm_items(("Midway", 32.05, -81.4), ("Midway", 34.2, -83.5), ("Fortson", 32.6, -84.95))
        rec = record()
        rec["description"] = "At Midway, replace the protection on the Fortson 115kV line."
        self.assertEqual([d["name"] for d in geocode.described_stations(rec, osm)], ["Fortson"])
        # the planning zone leaves one Midway and puts Fortson out of reach
        self.assertEqual([d["name"] for d in geocode.described_stations(rec, osm, anchor={"lat": 34.2, "lon": -83.5})], ["Midway"])

    def test_description_places_a_project_only_when_its_own_stations_cannot_be_placed(self):
        osm = osm_items(("Ohara", 33.4, -84.3), ("Scherer", 33.06, -83.8))
        rec = record("CC - TOMOCHICHI 500/230kV SOLUTION")
        rec["description"] = "Build the new Tomochichi 500/230kV station splitting the Ohara - Scherer 500kV line."
        with patch.object(geocode, "nominatim", return_value=[]):
            build.locate([rec], osm, {}, None, offline=True)
        self.assertEqual(rec["locatedBy"], "description")
        self.assertEqual(rec["locationConfidence"], "low")
        self.assertGreaterEqual(rec["radiusMi"], geocode.DESC_RADIUS_MIN)
        self.assertIsNone(rec["endpoints"][0]["point"])


class PlacementTests(unittest.TestCase):
    def locate(self, eps, miles=10, traced=10):
        rec = record()
        rec["miles"] = miles
        grid = Mock()
        grid.route.return_value = {"miles": traced, "coords": [[33, -84], [33.1, -84]]}
        with patch.object(build, "geocode_project", return_value=copy.deepcopy(eps)):
            build.locate([rec], Mock(), {}, grid, offline=True)
        return rec, grid

    def test_partial_location_has_low_confidence_and_widened_radius(self):
        rec, grid = self.locate([endpoint(), endpoint("B", point=False)])
        self.assertIsNotNone(rec["center"])
        self.assertEqual(rec["locationConfidence"], "low")
        self.assertEqual(rec["radiusMi"], 5.5)
        self.assertEqual(rec["locationCompleteness"], {"located": 1, "total": 2})
        grid.route.assert_not_called()

    def test_none_is_reserved_for_unlocated_projects(self):
        rec, _ = self.locate([endpoint(point=False)])
        self.assertIsNone(rec["center"])
        self.assertEqual(rec["locationConfidence"], "none")

    def test_empty_extraction_has_a_visible_validation_warning(self):
        rec, _ = self.locate([])
        self.assertTrue(any("no station names" in issue["msg"] for issue in rec["issues"]))

    def test_town_and_ambiguous_endpoints_cannot_generate_routes(self):
        for weak in (endpoint("B", "town", "low"), endpoint("B", "osm-partial", "ambiguous")):
            with self.subTest(endpoint=weak):
                rec, grid = self.locate([endpoint(), weak])
                self.assertIsNone(rec["route"])
                grid.route.assert_not_called()

    def test_routes_do_not_skip_missing_intermediate_endpoints(self):
        rec, grid = self.locate([endpoint(), endpoint("B", point=False), endpoint("C")])
        self.assertIsNone(rec["route"])
        grid.route.assert_not_called()

    def test_large_route_length_mismatches_are_rejected_in_both_directions(self):
        for stated, traced in ((0.94, 10.23), (40, 5)):
            with self.subTest(stated=stated):
                rec, _ = self.locate([endpoint(), endpoint("B")], stated, traced)
                self.assertIsNone(rec["route"])
                self.assertTrue(any(i["msg"].startswith("route rejected:") for i in rec["issues"]))

    def test_station_route_retains_explicit_unverified_circuit_provenance(self):
        rec, _ = self.locate([endpoint(), endpoint("B")])
        self.assertEqual(rec["route"]["miles"], 10)
        self.assertFalse(rec["route"]["verified"])
        self.assertEqual(rec["route"]["method"], "osm-shortest-path")


class OffMapTests(unittest.TestCase):
    def test_a_project_outside_sc_and_ga_is_left_unplaced_with_one_issue(self):
        nc = record("ROCKY MOUNT - WILSON 230kV LINE REBUILD", uid="DUKE:R-1", state="NC")
        sc = record("Okatie - McIntosh 230kV Tie", uid="DESC:6888:1", state="SC")
        with patch.object(build, "geocode_project", return_value=[endpoint(), endpoint("B")]) as geo:
            build.locate(build.on_map([nc, sc]), Mock(), {}, None, offline=True)
        self.assertEqual([c.args[0]["uid"] for c in geo.call_args_list], ["DESC:6888:1"])   # never searched for in SC/GA
        self.assertIsNotNone(sc["center"])
        self.assertEqual((nc["center"], nc["radiusMi"], nc["locationConfidence"], nc["locatedBy"], nc["route"]), (None, None, "none", None, None))
        self.assertEqual([(e["name"], e["point"]) for e in nc["endpoints"]], [("ROCKY MOUNT", None), ("WILSON", None)])
        self.assertEqual(nc["locationCompleteness"], {"located": 0, "total": 2})
        self.assertEqual(len(nc["issues"]), 1)
        self.assertIn("covers South Carolina and Georgia only", nc["issues"][0]["msg"])
        self.assertIn("NC", nc["issues"][0]["msg"])


class GeorgiaTableTests(unittest.TestCase):
    def test_table_2_sponsor_is_read_as_printed_and_stored_as_its_code(self):
        # rows as pdftotext lays them out: the 2025 plan ends a row with the sponsor, the 2024 plan follows it with redacted costs
        for line, expected in (
            (" 211      2026       13188          DALTON 230kV NETWORK                      6/1/2026          Dalton", ("211", "2026", "13188", "DU")),
            ("   211      2026      18679            DU: EAST DALTON -                6/1/2026          DU            REDACTED                REDACTED", ("211", "2026", "18679", "DU")),
            (" 208      2027       20717             SOLUTION (NETWORK                                         GPC", ("208", "2027", "20717", "GPC")),
            (" 208      2026       21022                                                                       GPC", ("208", "2026", "21022", "GPC")),
        ):
            with self.subTest(line=line):
                m = parse_ga.ROW.match(line)
                self.assertEqual((*m.group(1, 2, 3), parse_ga.sponsor_code(m.group(4))), expected)
        self.assertEqual(parse_ga.sponsor_code("Georgia Power"), "GPC")
        self.assertEqual(parse_ga.sponsor_code("Georgia"), "Georgia")   # fits several codes: kept as printed
        self.assertEqual(parse_ga.sponsor_code("Oglethorpe Power"), "Oglethorpe Power")   # fits none


class ZoneTests(unittest.TestCase):
    def placed(self, zone, lat, lon, n):
        rec = record(f"P{n}", uid=f"X:{n}", plan="x")
        rec.update(zone=zone, center={"lat": lat, "lon": lon}, radiusMi=0.5, locationConfidence="high", route=None, locatedBy="endpoints",
                   endpoints=[endpoint()], locationCompleteness={"located": 1, "total": 1})
        return rec

    def test_a_broad_region_is_not_a_planning_zone(self):
        # a compact zone around Atlanta with one project matched to a Savannah name, and a region some 300 mi long
        atlanta = [self.placed("206", 33.75 + d, -84.39 + d, i) for i, d in enumerate((0, 0.05, -0.05, 0.1, -0.1))]
        stray = self.placed("206", 32.05, -81.10, 5)
        region = [self.placed("West Region", lat, -84.0, 10 + i) for i, lat in enumerate((31.0, 31.8, 32.6, 33.4, 34.2, 35.4))]
        recs = atlanta + [stray] + region
        zones = build.planning_zones(recs)
        self.assertEqual(zones, {"206"})
        self.assertEqual(set(build.zone_anchors(recs, zones)), {"206"})
        build.unplace_zone_outliers(recs, zones)
        self.assertIsNone(stray["center"])
        self.assertTrue(stray["issues"][-1]["msg"].endswith("so it is left unplaced"))
        self.assertTrue(all(r["center"] for r in atlanta + region))   # the region's 35.4 N project is 166 mi from its median
        self.assertTrue(all(not r["issues"] for r in atlanta + region))


class IdentityTests(unittest.TestCase):
    def test_only_reused_printed_ids_change(self):
        recs = [record(uid="DESC:6809M:19", state="SC"), record(uid="DESC:6809M:48", state="SC"), record(uid="DESC:6888:41", state="SC"), record(uid="GA:99999")]
        build.assign_app_ids(recs)
        self.assertEqual([r["app_id"] for r in recs], ["DESC-6809M-19", "DESC-6809M-48", "DESC-6888", "GA-99999"])
        self.assertEqual(recs[0]["legacy_id"], "DESC-6809M")
        self.assertNotIn("legacy_id", recs[2])

    def test_santee_cooper_ids_come_from_the_title_key(self):
        recs = [record(uid="SCPSA:BLUFFTON-IMPROVEMENTS", state="SC"), record(uid="DESC:6888:41", state="SC")]
        build.assign_app_ids(recs)
        self.assertEqual([r["app_id"] for r in recs], ["SCPSA-BLUFFTON-IMPROVEMENTS", "DESC-6888"])

    def test_override_key_and_link_are_the_uid_prefix_and_printed_id_for_every_plan(self):
        # the same values the old state-based rules gave: DESC:<ProjectID> without the item suffix, GA:<TEAMS> = uid
        for uid, state, key, link in (("DESC:6853B-F:12", "SC", "DESC:6853B-F", "DESC-6853B-F"), ("DESC:06810F:3", "SC", "DESC:06810F", "DESC-06810F"),
                                      ("GA:21319", "GA", "GA:21319", "GA-21319"), ("SANTEE:T-44", "SC", "SANTEE:T-44", "SANTEE-T-44")):
            with self.subTest(uid=uid):
                rec = record(uid=uid, state=state)
                self.assertEqual(build.ov_key(rec), key)
                build.assign_app_ids([rec])
                self.assertEqual(rec["app_id"], link)
        recs = [record(uid="SANTEE:T-44:2", state="SC"), record(uid="SANTEE:T-44:9", state="SC")]
        build.assign_app_ids(recs)
        self.assertEqual([(r["app_id"], r["legacy_id"]) for r in recs], [("SANTEE-T-44-2", "SANTEE-T-44"), ("SANTEE-T-44-9", "SANTEE-T-44")])

    def test_every_override_names_a_key_the_general_rule_can_produce(self):
        ov = json.loads((ROOT / "data/overrides.json").read_text())
        for section in ("endpoints", "endpoint_notes", "radius", "host_line"):
            for k in ov.get(section, {}):
                with self.subTest(section=section, key=k):
                    self.assertRegex(k, r"^[A-Z]+:[^:]+$")

    def test_unresolvable_duplicate_records_fail_the_build(self):
        with self.assertRaises(ValueError):
            build.assign_app_ids([record(), record()])

    def test_committed_dataset_has_unique_ids_and_consistent_confidence(self):
        projects = json.loads((ROOT / "data/projects.json").read_text())["projects"]
        self.assertEqual(len(projects), len({p["id"] for p in projects}))
        for project in projects:
            with self.subTest(project=project["id"]):
                self.assertEqual(project["center"] is None, project["locationConfidence"] == "none")
                if project["route"]:
                    self.assertTrue(all(e["point"] and e["method"] != "town" and e["confidence"] in ("medium", "high") for e in project["endpoints"]))


if __name__ == "__main__":
    unittest.main()
