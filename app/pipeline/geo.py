"""Small geometry helpers and the static list of nuclear plants near Loiret."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088

# Nuclear power plants near Loiret (WGS84). They change rarely, so they are kept here
# instead of being fetched. Saint-Laurent and Dampierre were fitted to the distances of the
# original data file (max error 0.07 km); Belleville uses its published coordinates.
CENTRALES = {
    "Saint Laurent des Eaux": (47.7178, 1.5762),
    "Dampierre en Burly": (47.7328, 2.5187),
    "Belleville sur Loire": (47.5103, 2.8750),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def nearest_centrale(lat: float, lon: float) -> tuple[str, float]:
    name, (clat, clon) = min(
        CENTRALES.items(), key=lambda kv: haversine_km(lat, lon, *kv[1])
    )
    return name, haversine_km(lat, lon, clat, clon)
