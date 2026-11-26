#!/usr/bin/env python3
"""Build the data behind the "zasięg zajezdni" map: how far each WTP bus depot reaches.

Reads the 436.pl GTFS feed (feed/warsaw.zip, https://436.pl/gtfs/warsaw.zip) and, for one
reference date, counts every daytime bus trip per depot on every stretch of street. The
result goes to site/data.js, which site/index.html draws on an OSM basemap.

How it works:
- Trips: bus routes running on DATE, without night (N*), local (L*) and substitute (Z*)
  lines and without the depot runs (variant codes TD = pull-out, TZ = pull-in). The depot
  is trips.txt's non-standard depot_id, labelled like przydzial-brygad does, with all of
  an operator's yards folded together (Mob*, Relobus*).
- Street graph: shapes of different lines share their points exactly (they are routed
  over the same OSM ways), so a shape is cut into edges between consecutive points rounded
  to 5 decimals, and edges of all shapes are merged regardless of direction. Each edge
  carries trips per depot; runs of edges with the same load are chained and simplified.
- Ribbons: on a stretch served by several depots each one gets its own parallel ribbon,
  width ~ sqrt(trips), side by side in a fixed depot order (precomputed offsets).
- Yards: where the TZ/TD trips of each depot code end/start. The contractors have no
  depot runs in the feed, so they get no yard; the spread of every depot is measured from
  its trip-km weighted centre instead.

Standard library only.
"""

import collections
import csv
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

FEED = Path("feed/warsaw.zip")
OUT = Path("site/data.js")
DATE = "20261201"

BUS_ROUTE_TYPE = "3"
SKIP_LINE = re.compile(r"^[NLZ]")
DEPOT_RUN = ("TD", "TZ")

# depot_id -> label, as in przydzial-brygad (R-n is the public MZA numbering).
MZA = {
    "R-7(W)": "R-1",
    "R11(K)": "R-2",
    "R10(O)": "R-3",
    "R13(S)": "R-4",
    "R14(P)": "R-6",
    "PKS_A82": "PKS Grodzisk",
    "KMŁ K-26": "KMŁ",
}
DEPOT_NAME = {
    "R-1": "R-1 Woronicza",
    "R-2": "R-2 Kleszczowa",
    "R-3": "R-3 Ostrobramska",
    "R-4": "R-4 Stalowa",
    "R-6": "R-6 Płochocińska",
    "Mobilis": "Mobilis",
    "PKS Grodzisk": "PKS Grodzisk Mazowiecki",
    "Relobus": "Relobus",
    "KMŁ": "Komunikacja Miejska Łomianki",
}
DEPOT_ORDER = list(DEPOT_NAME)
# przydzial-brygad's hues, deepened for lines on a map. Validated as a 9-slot categorical
# palette (all pairs, light surface): normal-vision ΔE >= 15; colour-blind separation is
# in the 6–8 "needs secondary encoding" band, covered by tooltips and the depot solo mode.
DEPOT_COLOR = {
    "R-1": "#2a8fea",
    "R-2": "#4d8a52",
    "R-3": "#cd5c24",
    "R-4": "#fa2878",
    "R-6": "#6d4b93",
    "Mobilis": "#dea86b",
    "PKS Grodzisk": "#804007",
    "Relobus": "#61be15",
    "KMŁ": "#b070b1",
}


def depot_label(depot_id):
    if depot_id in MZA:
        return MZA[depot_id]
    if depot_id.startswith("Mob"):
        return "Mobilis"
    if depot_id.startswith("Relobus"):
        return "Relobus"
    return None


# Ribbon geometry, in pixels at the reference zoom (the page scales it with zoom).
def ribbon_width(trips):
    return round(0.8 + 0.42 * math.sqrt(trips), 2)


RIBBON_GAP = 0.5
SIMPLIFY_M = 4.0
YARD_MERGE_M = 1500

# Local equirectangular projection, metres; plenty accurate over ~80 km.
LAT0 = 52.23
KX = 111320 * math.cos(math.radians(LAT0))
KY = 110540


def xy(p):
    return p[1] * KX, p[0] * KY


def dist_m(a, b):
    (ax, ay), (bx, by) = xy(a), xy(b)
    return math.hypot(ax - bx, ay - by)


class Feed:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)

    def rows(self, name):
        with self.zip.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def load_trips(feed):
    routes = {r["route_id"]: r for r in feed.rows("routes.txt")}
    services = {r["service_id"] for r in feed.rows("calendar_dates.txt")
                if r["date"] == DATE and r["exception_type"] == "1"}
    if not services:
        sys.exit(f"no service on {DATE} in the feed")
    load = collections.defaultdict(collections.Counter)  # shape -> depot -> trips
    lines = collections.defaultdict(lambda: collections.defaultdict(set))  # shape -> depot -> lines
    depot_runs = collections.defaultdict(list)  # depot -> [(shape, "TZ"/"TD", headsign)]
    unmapped = collections.Counter()
    depot_lines = collections.defaultdict(set)
    depot_trips = collections.Counter()
    for t in feed.rows("trips.txt"):
        if t["service_id"] not in services:
            continue
        route = routes[t["route_id"]]
        line = route["route_short_name"]
        if route["route_type"] != BUS_ROUTE_TYPE or SKIP_LINE.match(line):
            continue
        depot = depot_label(t.get("depot_id") or "")
        if depot is None:
            unmapped[t.get("depot_id")] += 1
            continue
        kind = t["variant_code"][:2]
        if kind in DEPOT_RUN:
            depot_runs[depot].append((t["shape_id"], kind, t["trip_headsign"]))
            continue
        load[t["shape_id"]][depot] += 1
        lines[t["shape_id"]][depot].add(line)
        depot_lines[depot].add(line)
        depot_trips[depot] += 1
    if unmapped:
        print(f"warning: trips with an unknown depot_id: {dict(unmapped)}", file=sys.stderr)
    return load, lines, depot_runs, depot_lines, depot_trips


def load_shapes(feed, wanted):
    pts = collections.defaultdict(list)
    for s in feed.rows("shapes.txt"):
        if s["shape_id"] in wanted:
            pts[s["shape_id"]].append((int(s["shape_pt_sequence"]),
                                       round(float(s["shape_pt_lat"]), 5),
                                       round(float(s["shape_pt_lon"]), 5)))
    out = {}
    for sid, p in pts.items():
        p.sort()
        out[sid] = [(lat, lon) for _, lat, lon in p]
    return out


def find_yards(depot_runs, shapes):
    """Yards per depot: clusters of where pull-ins end and pull-outs start."""
    yards = {}
    for depot, runs in depot_runs.items():
        ends = collections.Counter()
        points = collections.defaultdict(collections.Counter)
        headsigns = collections.defaultdict(collections.Counter)
        for sid, kind, headsign in runs:
            pts = shapes.get(sid)
            if not pts:
                continue
            p = pts[-1] if kind == "TZ" else pts[0]
            # ~500 m buckets so the stops around one yard fall together
            key = (round(p[0] / 0.005), round(p[1] / 0.008))
            ends[key] += 1
            points[key][p] += 1
            if kind == "TZ":
                headsigns[key][headsign] += 1
        total = sum(ends.values())
        found = []
        for key, n in ends.most_common():
            if n < max(8, 0.08 * total):
                break
            (lat, lon), _ = points[key].most_common(1)[0]
            near = next((y for y in found if dist_m((y["lat"], y["lon"]), (lat, lon)) < YARD_MERGE_M), None)
            if near:  # the same yard's other gate
                near["runs"] += n
                continue
            name = headsigns[key].most_common(1)[0][0] if headsigns[key] else ""
            found.append({"lat": lat, "lon": lon, "name": name, "runs": n})
        yards[depot] = found
    return yards


def build_graph(load, lines, shapes):
    edges = collections.defaultdict(collections.Counter)  # (a, b) -> depot -> trips
    edge_lines = collections.defaultdict(lambda: collections.defaultdict(set))
    for sid, by_depot in load.items():
        pts = shapes.get(sid)
        if not pts:
            continue
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            e = edges[key]
            for depot, n in by_depot.items():
                e[depot] += n
                edge_lines[key][depot] |= lines[sid][depot]
    return edges, edge_lines


def signature(counter, lines_by_depot):
    return tuple(sorted((d, n, tuple(sorted(lines_by_depot[d], key=line_sort)))
                        for d, n in counter.items()))


def line_sort(line):
    m = re.match(r"(\D*)(\d+)", line)
    return (m.group(1), int(m.group(2))) if m else (line, 0)


def chains(edges, edge_lines):
    """Merge edges into maximal paths of equal load; yields (points, signature)."""
    sig = {k: signature(edges[k], edge_lines[k]) for k in edges}
    adj = collections.defaultdict(list)
    for a, b in edges:
        adj[a].append((a, b) if a < b else (b, a))
        adj[b].append((a, b) if a < b else (b, a))

    def through(node, s):
        """A node a chain passes through: exactly two edges, both with signature s."""
        inc = adj[node]
        return len(inc) == 2 and sig[inc[0]] == s and sig[inc[1]] == s

    used = set()
    out = []
    for start_edge in edges:
        if start_edge in used:
            continue
        s = sig[start_edge]
        used.add(start_edge)
        path = list(start_edge)
        for forward in (True, False):
            while True:
                end = path[-1] if forward else path[0]
                if not through(end, s):
                    break
                nxt = next((e for e in adj[end] if e not in used), None)
                if nxt is None:
                    break
                used.add(nxt)
                other = nxt[1] if nxt[0] == end else nxt[0]
                if forward:
                    path.append(other)
                else:
                    path.insert(0, other)
        out.append((path, s))
    return out


def simplify(pts, tol):
    """Douglas–Peucker in local metres."""
    if len(pts) < 3:
        return pts
    p = [xy(q) for q in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = p[i], p[j]
        dx, dy = x2 - x1, y2 - y1
        seg = math.hypot(dx, dy)
        best, idx = -1, -1
        for k in range(i + 1, j):
            x0, y0 = p[k]
            d = (abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / seg) if seg else math.hypot(x0 - x1, y0 - y1)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [q for q, k in zip(pts, keep) if k]


def ribbons(chain_list):
    features = []
    for pts, sig in chain_list:
        coords = [[lon, lat] for lat, lon in simplify(pts, SIMPLIFY_M)]
        rows = sorted(sig, key=lambda r: DEPOT_ORDER.index(r[0]))
        widths = [ribbon_width(n) for _, n, _ in rows]
        total = sum(widths) + RIBBON_GAP * (len(widths) - 1)
        pos = -total / 2
        tip = [[d, n, ", ".join(ls)] for d, n, ls in rows]
        for (depot, n, _), w in zip(rows, widths):
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"d": depot, "n": n, "w": w,
                               "o": round(pos + w / 2, 2) if len(rows) > 1 else 0,
                               "t": json.dumps(tip, ensure_ascii=False, separators=(",", ":"))},
            })
            pos += w + RIBBON_GAP
    return features


def depot_stats(edges, yards, depot_lines, depot_trips):
    """Per depot: size, and how far its network spreads.

    Spread is measured from the depot's centre of gravity (trip-km weighted), the same
    for MZA and the contractors, whose yards the feed does not reveal. For depots with a
    known yard, the farthest point from it is reported too.
    """
    segs = collections.defaultdict(list)  # depot -> [(midpoint, trip-km, a, b)]
    km = collections.Counter()
    for (a, b), by_depot in edges.items():
        length = dist_m(a, b) / 1000
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for depot, n in by_depot.items():
            km[depot] += length
            segs[depot].append((mid, n * length))
    stats = {}
    for depot in DEPOT_ORDER:
        s = segs[depot]
        total = sum(w for _, w in s)
        centre = (sum(p[0] * w for p, w in s) / total, sum(p[1] * w for p, w in s) / total)
        dist = sorted((dist_m(p, centre) / 1000, w) for p, w in s)
        acc, median = 0, None
        for d, w in dist:
            acc += w
            if acc >= 0.5 * total:
                median = d
                break
        far_centre = max(s, key=lambda pw: dist_m(pw[0], centre))[0]
        entry = {
            "lines": sorted(depot_lines[depot], key=line_sort),
            "trips": depot_trips[depot],
            "networkKm": round(km[depot]),
            "tripKm": round(total),
            "centre": [round(centre[1], 5), round(centre[0], 5)],
            "medianKm": round(median, 1),
            "maxKm": round(dist_m(far_centre, centre) / 1000, 1),
        }
        ys = yards.get(depot)
        if ys:
            far = max(s, key=lambda pw: min(dist_m(pw[0], (y["lat"], y["lon"])) for y in ys))[0]
            entry["yardMaxKm"] = round(min(dist_m(far, (y["lat"], y["lon"])) for y in ys) / 1000, 1)
            entry["yardFarthest"] = [round(far[1], 5), round(far[0], 5)]
        stats[depot] = entry
    return stats


def main():
    feed = Feed(FEED)
    load, lines, depot_runs, depot_lines, depot_trips = load_trips(feed)
    wanted = set(load) | {sid for runs in depot_runs.values() for sid, _, _ in runs}
    shapes = load_shapes(feed, wanted)
    yards = find_yards(depot_runs, shapes)
    edges, edge_lines = build_graph(load, lines, shapes)
    chain_list = chains(edges, edge_lines)
    feeds_version = next(feed.rows("feed_info.txt"))["feed_version"]
    data = {
        "date": DATE,
        "feedVersion": feeds_version,
        "depots": [{"id": d, "name": DEPOT_NAME[d], "color": DEPOT_COLOR[d], "yards": yards.get(d, [])}
                   for d in DEPOT_ORDER],
        "stats": depot_stats(edges, yards, depot_lines, depot_trips),
        "ribbons": {"type": "FeatureCollection", "features": ribbons(chain_list)},
    }
    OUT.parent.mkdir(exist_ok=True)
    js = "window.ZASIEG = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUT.write_text(js, encoding="utf-8")
    print(f"{len(load)} shapes, {len(edges)} edges, {len(chain_list)} chains, "
          f"{len(data['ribbons']['features'])} ribbons, "
          f"{OUT.stat().st_size / 1e6:.1f} MB")
    for d in DEPOT_ORDER:
        s = data["stats"][d]
        ys = "; ".join(f"{y['name']} ({y['lat']},{y['lon']}) ×{y['runs']}" for y in yards.get(d, []))
        print(f"  {d:13} {len(s['lines']):3} lines {s['trips']:5} trips {s['networkKm']:5} km "
              f"med {s['medianKm']:5} max {s['maxKm']:5} "
              f"yard max {s.get('yardMaxKm', '-'):>5}  yards: {ys}")


if __name__ == "__main__":
    main()
