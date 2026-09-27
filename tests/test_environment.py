"""Geometry checks for pipeline/environment.py. Run: python3 -m unittest discover tests"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import environment as env  # noqa: E402

SQUARE = [[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]          # clockwise: an Esri outer ring
HOLE = [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]]   # counterclockwise: a hole


class Geometry(unittest.TestCase):
    def test_even_odd_holes(self):
        self.assertTrue(env.inside_rings((0.25, 0.25), [SQUARE, HOLE]))
        self.assertFalse(env.inside_rings((1, 1), [SQUARE, HOLE]))
        self.assertFalse(env.inside_rings((3, 1), [SQUARE, HOLE]))

    def test_clip_to_box(self):
        clipped = env.clip_ring(SQUARE, (1, 1, 3, 3))
        xs = sorted({p[0] for p in clipped}); ys = sorted({p[1] for p in clipped})
        self.assertEqual((xs[0], xs[-1], ys[0], ys[-1]), (1, 2, 1, 2))
        self.assertEqual(env.clip_ring(SQUARE, (5, 5, 6, 6)), [])

    def test_clip_to_convex(self):
        tri = [[0, 0], [4, 0], [0, 4]]   # counterclockwise
        clipped = env.clip_convex(SQUARE, tri)
        self.assertAlmostEqual(abs(env.ring_area(clipped)), 4.0)   # the square lies inside the triangle
        half = env.clip_convex(SQUARE, [[1, -1], [3, -1], [3, 3], [1, 3]])
        self.assertAlmostEqual(abs(env.ring_area(half)), 2.0)

    def test_multipolygon_groups_holes(self):
        polys = env.multipolygon([SQUARE, HOLE, [[5, 5], [5, 6], [6, 6], [6, 5], [5, 5]]])
        self.assertEqual(len(polys), 2)
        self.assertEqual(len(polys[0]), 2)

    def test_clip_path(self):
        parts = env.clip_path([[-1, 1], [3, 1]], (0, 0, 2, 2))
        self.assertEqual(parts, [[[0.0, 1.0], [2.0, 1.0]]])

    def test_path_samples_cover_length(self):
        path = [[-81.0, 32.0], [-81.0, 32.1]]
        total = sum(d for _, d in env.path_samples(path))
        self.assertAlmostEqual(total, env.haversine_mi(32.0, -81.0, 32.1, -81.0), places=6)

    def test_corridor_quads_do_not_overlap_on_a_straight_line(self):
        path = [[-81.0, 32.0], [-80.99, 32.0], [-80.98, 32.0]]
        quads, outline = env.corridor(path, 0.1)
        self.assertEqual(len(quads), 1)   # collinear points simplify away
        self.assertEqual(outline[0], outline[-1])

    def test_site_share_of_a_fully_covered_circle(self):
        c = (-81.0, 32.0)
        box = env.bbox_around([c], 1)
        cover = [{"a": {"WETLAND_TYPE": "Freshwater Forested/Shrub Wetland"}, "rings": [[[box[0], box[1]], [box[0], box[3]], [box[2], box[3]], [box[2], box[1]], [box[0], box[1]]]]}]
        got = {k: [] for k in env.LAYERS}
        got["wetlands"] = cover
        got["flood"] = [{"a": {"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T"}, "rings": cover[0]["rings"]}]
        out = env.read_site(c, got)
        self.assertEqual(out["wetlands"]["share"], 1.0)
        self.assertEqual(out["wetlands"]["waterShare"], 0.0)
        self.assertEqual(out["flood"]["zone"], "AE")
        self.assertTrue(out["flood"]["sfha"])
        self.assertEqual(out["flood"]["sfhaShare"], 1.0)



class PairingRange(unittest.TestCase):
    def test_projects_are_in_range_only_across_states(self):
        rec = lambda uid, plan, state, lat: {"uid": uid, "plan": plan, "state": state, "center": {"lat": lat, "lon": -81.0}, "radiusMi": 0.5}
        recs = [rec("GA:1", "ga", "GA", 32.0), rec("GA:2", "ga", "GA", 32.1),
                rec("DESC:near", "desc", "SC", 32.15),
                rec("SANTEE:1", "santee", "SC", 33.0), rec("DESC:9:1", "desc", "SC", 33.1),
                rec("DESC:8:2", "desc", "SC", 35.0)]
        self.assertEqual(env.in_pairing_range(recs), {"GA:1", "GA:2", "DESC:near"})

    def test_an_unplaced_project_is_not_said_to_be_far_from_the_others(self):
        recs = [{"uid": "GA:1", "plan": "ga", "state": "GA", "center": None, "radiusMi": None, "endpoints": []},
                {"uid": "DESC:8:2", "plan": "desc", "state": "SC", "center": {"lat": 35.0, "lon": -81.0}, "radiusMi": 0.5, "endpoints": []}]
        with tempfile.TemporaryDirectory() as d, patch.object(env, "_cache_path", Path(d) / "environment.json"), \
                patch.object(env, "domains"), patch.object(env, "write_evidence"), contextlib.redirect_stdout(io.StringIO()):
            env.check(recs, offline=True)
        self.assertEqual(recs[0]["environment"], {"checked": False, "reason": env.UNPLACED})
        self.assertEqual(recs[1]["environment"], {"checked": False, "reason": "not near any project in the other state"})


if __name__ == "__main__":
    unittest.main()
