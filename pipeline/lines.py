"""Trace a project's line along mapped OSM power lines between its two endpoints (border area only).

This is a secondary signal: it gives a route length to check against the length the utility states,
and a geometry for closest-approach between two projects. It never decides whether a pair qualifies.
"""
import heapq
import math

from common import CACHE, haversine_mi, load, xy_mi

SNAP_MI = 0.4   # a line "reaches" a station if a vertex lies within this distance of the station point


class Grid:
    def __init__(self):
        els = load(CACHE / "osm_lines_border.json")["elements"]
        self.adj = {}
        self.nodes = {}
        for w in els:
            if w["type"] != "way" or w.get("tags", {}).get("power") not in ("line", "cable"):
                continue
            g = w["geometry"]
            for a, b in zip(g, g[1:]):
                ka, kb = (round(a["lat"], 5), round(a["lon"], 5)), (round(b["lat"], 5), round(b["lon"], 5))
                d = haversine_mi(*ka, *kb)
                self.adj.setdefault(ka, []).append((kb, d))
                self.adj.setdefault(kb, []).append((ka, d))
        self.buckets = {}
        for k in self.adj:
            self.buckets.setdefault((int(k[0] * 20), int(k[1] * 20)), []).append(k)
        la = [k[0] for k in self.adj]; lo = [k[1] for k in self.adj]
        self.bbox = (min(la), max(la), min(lo), max(lo))

    def covers(self, p):
        return self.bbox[0] <= p["lat"] <= self.bbox[1] and self.bbox[2] <= p["lon"] <= self.bbox[3]

    def near(self, p):
        bi, bj = int(p["lat"] * 20), int(p["lon"] * 20)
        out = []
        for i in (bi - 1, bi, bi + 1):
            for j in (bj - 1, bj, bj + 1):
                for k in self.buckets.get((i, j), []):
                    if haversine_mi(p["lat"], p["lon"], *k) <= SNAP_MI:
                        out.append(k)
        return out

    def route(self, a, b, max_ratio=3.0):
        """Shortest path along mapped lines from station a to station b, or None."""
        if not (self.covers(a) and self.covers(b)):
            return None
        src, dst = self.near(a), set(self.near(b))
        if not src or not dst:
            return None
        straight = haversine_mi(a["lat"], a["lon"], b["lat"], b["lon"])
        limit = max(straight * max_ratio, 2.0)
        dist = {s: 0.0 for s in src}
        prev = {}
        pq = [(0.0, s) for s in src]
        heapq.heapify(pq)
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf) or d > limit:
                continue
            if u in dst:
                path = [u]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                path.reverse()
                return {"miles": round(d, 2), "coords": simplify(path)}
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return None


def simplify(path, tol_mi=0.05):
    """Douglas-Peucker in a local mile projection; keeps the JSON small."""
    if len(path) < 3:
        return [list(p) for p in path]
    lat0 = path[0][0]
    xy = [xy_mi(la, lo, lat0) for la, lo in path]

    def rec(i, j):
        ax, ay = xy[i]; bx, by = xy[j]
        best, idx = 0, None
        for k in range(i + 1, j):
            px, py = xy[k]
            L = math.hypot(bx - ax, by - ay) or 1e-9
            d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
            if d > best:
                best, idx = d, k
        if best > tol_mi:
            return rec(i, idx)[:-1] + rec(idx, j)
        return [i, j]
    return [[round(path[k][0], 5), round(path[k][1], 5)] for k in rec(0, len(path) - 1)]
