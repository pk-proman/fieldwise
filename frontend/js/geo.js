export function haversine(lat1, lng1, lat2, lng2) {
  const R = 6371000, r = Math.PI / 180, dp = (lat2 - lat1) * r, dl = (lng2 - lng1) * r;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}
