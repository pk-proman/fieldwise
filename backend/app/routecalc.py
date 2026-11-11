"""Turns raw GPS breadcrumbs into a trustworthy distance, stops and a light route line.
Rules are deliberately simple and explainable because the result drives expense claims:
  - ignore mock-location points and points with poor accuracy;
  - ignore tiny movements that are just GPS drift while standing still;
  - reject impossible jumps (faster than a car on a highway);
  - when the phone was silent for a long time (off, no GPS), do NOT count the missing stretch as tracked: report it separately as "estimated"."""
from .geo import haversine_m

MAX_ACCURACY_M = 60          # points less accurate than this are ignored
MIN_STEP_M = 15              # movement below this (or below the point's own accuracy, up to 40 m) is drift
MAX_SPEED_MS = 55            # 198 km/h: faster is a GPS jump
GAP_S = 600                  # silence longer than 10 minutes is a gap
ROAD_FACTOR = 1.3            # straight line to road distance for estimated gaps
STOP_RADIUS_M = 60
STOP_MIN_S = 300


def _rdp(pts, eps_m):
    """Ramer-Douglas-Peucker on (lat, lng) with metre tolerance (iterative; flat-earth projection is fine for this size)."""
    if len(pts) < 3:
        return pts
    import math
    lat0 = math.radians(pts[0][0])
    xy = [((p[1]) * 111320 * math.cos(lat0), p[0] * 110540) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = xy[a], xy[b]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy) or 1e-9
        far, idx = 0.0, -1
        for i in range(a + 1, b):
            d = abs(dy * xy[i][0] - dx * xy[i][1] + x2 * y1 - y2 * x1) / L
            if d > far:
                far, idx = d, i
        if far > eps_m and idx != -1:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def analyse(pings) -> dict:
    """pings: LocationPing-like objects ordered by ts (ts, lat, lng, accuracy, mock, gps_on)."""
    total = len(pings)
    good = [p for p in pings if p.lat is not None and p.lng is not None and not p.mock and (p.accuracy is None or p.accuracy <= MAX_ACCURACY_M)]
    mock = sum(1 for p in pings if p.mock)
    out = {"distance_km": 0.0, "est_km": 0.0, "moving_min": 0, "stop_min": 0, "gap_min": 0, "points": total, "used": 0, "dropped": total - len(good), "mock_points": mock,
           "stops": [], "polyline": [], "quality": 0, "first_at": None, "last_at": None, "start": None, "end": None, "far_from_start_km": 0.0}
    if not good:
        return out
    out["first_at"], out["last_at"] = good[0].ts, good[-1].ts
    out["start"], out["end"] = [good[0].lat, good[0].lng], [good[-1].lat, good[-1].lng]
    kept = [good[0]]
    tracked = est = moving_s = gap_s = 0.0
    prev = good[0]
    for p in good[1:]:
        dt = (p.ts - prev.ts).total_seconds()
        if dt <= 0:
            continue
        d = haversine_m(prev.lat, prev.lng, p.lat, p.lng)
        if dt > GAP_S:                       # the phone was silent
            gap_s += dt
            if d >= 200:
                est += d * ROAD_FACTOR
            kept.append(p)
            prev = p
            continue
        if d / dt > MAX_SPEED_MS:            # impossible jump: skip this point, keep the previous anchor
            out["dropped"] += 1
            continue
        if d < max(MIN_STEP_M, min(p.accuracy or 0, 40)):   # drift while standing still: do not move the anchor
            continue
        tracked += d
        moving_s += dt
        kept.append(p)
        prev = p
    out["distance_km"] = round(tracked / 1000, 2)
    out["est_km"] = round(est / 1000, 2)
    out["moving_min"] = round(moving_s / 60)
    out["gap_min"] = round(gap_s / 60)
    out["used"] = len(kept)
    # stops: stayed within STOP_RADIUS_M of an anchor for at least STOP_MIN_S
    anchor, t0, last = good[0], good[0].ts, good[0].ts
    def close_stop():
        dur = (last - t0).total_seconds()
        if dur >= STOP_MIN_S:
            out["stops"].append({"lat": round(anchor.lat, 5), "lng": round(anchor.lng, 5), "from": t0.isoformat() + "Z", "to": last.isoformat() + "Z", "min": round(dur / 60)})
    for p in good[1:]:
        if haversine_m(anchor.lat, anchor.lng, p.lat, p.lng) <= STOP_RADIUS_M:
            last = p.ts
        else:
            close_stop()
            anchor, t0, last = p, p.ts, p.ts
    close_stop()
    out["stop_min"] = sum(s["min"] for s in out["stops"])
    out["far_from_start_km"] = round(max(haversine_m(good[0].lat, good[0].lng, p.lat, p.lng) for p in good) / 1000, 2)
    # coverage: how much of the span between first and last point the phone was actually reporting
    span = max((good[-1].ts - good[0].ts).total_seconds(), 1)
    cover = 1 - min(gap_s / span, 1)
    acc = [p.accuracy for p in good if p.accuracy is not None]
    acc_pen = 0 if not acc else min(sum(1 for a in acc if a > 30) / len(acc), 1) * 20
    mock_pen = 30 if mock else 0
    out["quality"] = max(0, round(cover * 100 - acc_pen - mock_pen))
    out["polyline"] = [[round(p[0], 5), round(p[1], 5)] for p in _rdp([(p.lat, p.lng) for p in kept], 12)][:400]
    return out
