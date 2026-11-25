"""Nuclear power plants in operation in France, with their official coordinates.

Géorisques lists them only by commune (`installations_nucleaires?code_insee=`), so the sites
are named here by commune and resolved through the official commune API, then checked
against Géorisques: a site it does not return as a power plant is reported, not invented.
Fessenheim (closed in 2020) is not listed. Plants abroad near the border (Belgium,
Switzerland, Germany) are not in Géorisques and not covered yet.
"""
from __future__ import annotations

import logging
from typing import Any

from app.pipeline.client import Fetcher

logger = logging.getLogger(__name__)

GEO_API = "https://geo.api.gouv.fr/communes"
GEORISQUES = "https://georisques.gouv.fr/api/v1/installations_nucleaires"

# (commune hosting the plant, department code) for the 18 sites in operation.
# Penly is in Petit-Caux (Saint-Martin-en-Campagne merged into it in 2016).
SITES: list[tuple[str, str]] = [
    ("Belleville-sur-Loire", "18"), ("Braud-et-Saint-Louis", "33"), ("Saint-Vulbas", "01"),
    ("Cattenom", "57"), ("Avoine", "37"), ("Chooz", "08"), ("Civaux", "86"), ("Cruas", "07"),
    ("Dampierre-en-Burly", "45"), ("Flamanville", "50"), ("Golfech", "82"), ("Gravelines", "59"),
    ("Nogent-sur-Seine", "10"), ("Paluel", "76"), ("Petit-Caux", "76"),
    ("Saint-Maurice-l'Exil", "38"), ("Saint-Laurent-Nouan", "41"), ("Saint-Paul-Trois-Châteaux", "26"),
]
POWER_PLANT = "Centrales nucléaires"


def display_name(site: str) -> str:
    """"Dampierre-en-Burly" -> "Dampierre en Burly", the spelling already in the data."""
    return site.replace("-", " ")


def fetch_plants(fetcher: Fetcher) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """{name: (lat, lon)} of the plants found, and the sites that could not be confirmed."""
    plants: dict[str, tuple[float, float]] = {}
    problems = []
    for commune, departement in SITES:
        found: list[dict[str, Any]] = fetcher.get_json(
            GEO_API, {"nom": commune, "codeDepartement": departement, "fields": "code,nom", "limit": 5}
        ) or []
        exact = [c for c in found if c["nom"].lower() == commune.lower()] or found[:1]
        if not exact:
            problems.append(f"{commune} ({departement}): commune not found")
            continue
        installations = fetcher.get_json(GEORISQUES, {"code_insee": exact[0]["code"]}) or []
        reactors = [i for i in installations if i.get("typeInstallationNucleaire") == POWER_PLANT]
        if not reactors:
            problems.append(f"{commune} ({departement}): no power plant in Géorisques")
            continue
        site = reactors[0]
        name = display_name(site.get("site") or commune)
        plants[name] = (float(site["latitude"]), float(site["longitude"]))
    return plants, problems
