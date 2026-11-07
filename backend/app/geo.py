import math


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def path_km(points) -> float:
    """points: iterable of (lat,lng). Ignores GPS jitter < 15m and teleports > 300 km/h-equivalents handled by caller."""
    total, prev = 0.0, None
    for p in points:
        if prev:
            d = haversine_m(prev[0], prev[1], p[0], p[1])
            if d >= 15:
                total += d
        prev = p
    return total / 1000.0


def optimize_route(start, stops):
    """Nearest-neighbour seed + 2-opt improvement. start=(lat,lng); stops=[{id,lat,lng,...}]. Returns (ordered, km)."""
    if not stops:
        return [], 0.0
    pts = [{"lat": start[0], "lng": start[1], "_start": True}] + list(stops)
    n = len(pts)
    D = [[haversine_m(a["lat"], a["lng"], b["lat"], b["lng"]) for b in pts] for a in pts]
    order, left = [0], set(range(1, n))
    while left:
        nxt = min(left, key=lambda j: D[order[-1]][j])
        order.append(nxt)
        left.remove(nxt)
    improved = True
    while improved:  # open-path 2-opt with fixed start
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = order[i - 1], order[i]
                c = order[j]
                d = order[j + 1] if j + 1 < n else None
                old = D[a][b] + (D[c][d] if d is not None else 0)
                new = D[a][c] + (D[b][d] if d is not None else 0)
                if new + 1e-6 < old:
                    order[i:j + 1] = reversed(order[i:j + 1])
                    improved = True
    total = sum(D[order[k]][order[k + 1]] for k in range(n - 1))
    return [pts[i] for i in order[1:]], total / 1000.0
