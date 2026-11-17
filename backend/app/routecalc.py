"""Turns raw GPS breadcrumbs into a trustworthy distance, stops and a light route line.
Rules are deliberately simple and explainable because the result drives expense claims:
  - ignore mock-location points and points with poor accuracy;
  - ignore tiny movements that are just GPS drift while standing still;
  - reject impossible jumps (faster than a car on a highway);
  - when the phone was silent for a long time (off, no GPS), do NOT count the missing stretch as tracked: report it separately as "estimated"."""
from datetime import timedelta
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


# ---------------------------------------------------------------- phone reachability
REACH_GAP_MIN = 10           # no data for longer than this = the phone could not be reached
MIN_FLAG_MIN = 2             # internet / GPS flags shorter than this are noise
LOW_BATTERY = 15


def connectivity(pings, start=None, end=None, gap_min: int = REACH_GAP_MIN) -> dict:
    """What happened to the phone between `start` and `end` (datetimes, naive UTC) from the breadcrumbs it sent.
    Three kinds of episode, each with when it began, how long it lasted and where the person last was:
      unreachable: no data at all for more than `gap_min` minutes (phone off, app stopped, no signal and no GPS);
      no_internet: the phone itself reported being offline (the breadcrumbs were saved and sent later);
      gps_off:     the phone reported its location switch off.
    A ping stands for the time up to the next ping; when that is longer than `gap_min` minutes the silence is counted as unreachable instead."""
    cap = gap_min * 60
    pts = sorted(pings, key=lambda p: p.ts)
    if not pts:
        return {"episodes": [], "totals": {"unreachable_min": 0, "no_internet_min": 0, "gps_off_min": 0, "window_min": 0, "reporting_pct": None, "last_battery": None}}
    start = start or pts[0].ts
    end = end or pts[-1].ts
    iso = lambda t: t.isoformat() + "Z"
    pos = lambda p: [round(p.lat, 5), round(p.lng, 5)] if p.lat is not None and p.lng is not None else None
    episodes = []

    def last_pos(i):                     # last known position at or before ping i
        for j in range(i, -1, -1):
            if pts[j].lat is not None:
                return pos(pts[j])
        return None

    def first_pos(i):
        for j in range(i, len(pts)):
            if pts[j].lat is not None:
                return pos(pts[j])
        return None

    unreach = 0.0
    # silent stretches: before the first ping, between pings, after the last ping
    marks = [(start, None, None)] if (pts[0].ts - start).total_seconds() > cap else []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if (b.ts - a.ts).total_seconds() > cap and a.ts >= start and b.ts <= end:
            minutes = round((b.ts - a.ts).total_seconds() / 60)
            unreach += minutes
            episodes.append({"kind": "unreachable", "from": iso(a.ts), "to": iso(b.ts), "minutes": minutes, "at": last_pos(i), "to_at": first_pos(i + 1), "battery": a.battery})
    if marks:
        minutes = round((pts[0].ts - start).total_seconds() / 60)
        unreach += minutes
        episodes.append({"kind": "unreachable", "from": iso(start), "to": iso(pts[0].ts), "minutes": minutes, "at": None, "to_at": first_pos(0), "battery": None, "note": "no data after check-in"})
    if (end - pts[-1].ts).total_seconds() > cap:
        minutes = round((end - pts[-1].ts).total_seconds() / 60)
        unreach += minutes
        episodes.append({"kind": "unreachable", "from": iso(pts[-1].ts), "to": iso(end), "minutes": minutes, "at": last_pos(len(pts) - 1), "to_at": None, "battery": pts[-1].battery, "ongoing": True})

    def runs(kind, bad):
        """Consecutive pings for which `bad(p)` holds, each worth the time to the next ping (unless that is a silent gap)."""
        total, i, n = 0.0, 0, len(pts)
        while i < n:
            if not bad(pts[i]):
                i += 1
                continue
            j, secs = i, 0.0
            while j < n and bad(pts[j]):
                nxt = pts[j + 1].ts if j + 1 < n else end
                gap = (nxt - pts[j].ts).total_seconds()
                secs += gap if 0 <= gap <= cap else 0          # a longer silence is reported as "unreachable", not counted twice
                j += 1
            minutes = round(secs / 60)
            if minutes >= MIN_FLAG_MIN:
                total += minutes
                episodes.append({"kind": kind, "from": iso(pts[i].ts), "to": iso(pts[i].ts + timedelta(seconds=secs)), "minutes": minutes, "at": last_pos(i), "to_at": None, "battery": pts[i].battery})
            i = j
        return total

    net = runs("no_internet", lambda p: p.network == "offline")
    gps = runs("gps_off", lambda p: p.gps_on is False)
    for i, p in enumerate(pts):          # first time the battery dropped to a low level
        if p.battery is not None and p.battery <= LOW_BATTERY and (i == 0 or pts[i - 1].battery is None or pts[i - 1].battery > LOW_BATTERY):
            episodes.append({"kind": "battery_low", "from": iso(p.ts), "to": iso(p.ts), "minutes": 0, "at": pos(p), "to_at": None, "battery": p.battery})
    episodes.sort(key=lambda e: e["from"])
    window = max(round((end - start).total_seconds() / 60), 0)
    return {"episodes": episodes, "totals": {"unreachable_min": int(unreach), "no_internet_min": int(net), "gps_off_min": int(gps), "window_min": window,
            "reporting_pct": round(100 * max(window - unreach, 0) / window) if window else None, "last_battery": next((p.battery for p in reversed(pts) if p.battery is not None), None)}}
