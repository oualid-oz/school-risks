"""Small geometry helpers."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def nearest_centrale(lat: float, lon: float, plants: dict[str, tuple[float, float]]) -> tuple[str, float]:
    """Nearest nuclear power plant (from app.pipeline.nuclear.fetch_plants) and its distance."""
    name, (plat, plon) = min(plants.items(), key=lambda kv: haversine_km(lat, lon, *kv[1]))
    return name, haversine_km(lat, lon, plat, plon)
