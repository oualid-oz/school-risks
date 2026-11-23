"""data.education.gouv.fr sources: directory, enrolment, IPS and DNB results.

``fetch_*`` functions download raw rows; ``build_history`` turns them into the
``colleges.parquet`` table (one row per school and school year). The transforms only
take plain lists of dicts, so they are tested without network access.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.client import Fetcher

API = "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets"

# IPS is split over three datasets; later ones win when years overlap.
IPS_DATASETS = ("fr-en-ips_colleges", "fr-en-ips-colleges-ap2022", "fr-en-ips-colleges-ap2023")


def _in_list(field: str, values: list[str]) -> str:
    quoted = ", ".join(f'"{v}"' for v in sorted(values))
    return f"{field} in ({quoted})"


def _export(fetcher: Fetcher, dataset: str, where: str) -> list[dict[str, Any]]:
    rows = fetcher.get_json(f"{API}/{dataset}/exports/json", {"where": where})
    return rows or []


def fetch_all(fetcher: Fetcher, uais: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {
        "annuaire": _export(
            fetcher,
            "fr-en-annuaire-education",
            "code_departement='045' and type_etablissement='Collège'",
        ),
        "effectifs": _export(
            fetcher, "fr-en-college-effectifs-niveau-sexe-lv", _in_list("numero_college", uais)
        ),
        "ips": [
            {**row, "_dataset": ds}
            for ds in IPS_DATASETS
            for row in _export(fetcher, ds, _in_list("uai", uais))
        ],
        "dnb": _export(
            fetcher, "fr-en-dnb-par-etablissement", _in_list("numero_d_etablissement", uais)
        ),
        "ivac": _export(
            fetcher, "fr-en-indicateurs-valeur-ajoutee-colleges", _in_list("uai", uais)
        ),
    }


# ---- transforms -------------------------------------------------------------
def _year(value: Any) -> int:
    """'2016-2017', '2016' or 2016 -> 2016."""
    return int(str(value)[:4])


def _percent(value: Any) -> float | None:
    """'88,50%' / '80,6%' / 88.5 -> 88.5."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    return round(float(str(value).replace("%", "").replace(",", ".").strip()), 1)


def _int(value: Any) -> int | None:
    return None if value is None or value == "" else int(value)


def _float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def annuaire_by_uai(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for r in rows:
        statut = (r.get("statut_public_prive") or "").lower()
        out[r["identifiant_de_l_etablissement"]] = {
            "statut": "privé" if statut.startswith("priv") else "public",
            "lat": _float(r.get("latitude")),
            "lon": _float(r.get("longitude")),
            "etat": r.get("etat"),
            "nom_etablissement": r.get("nom_etablissement"),
            "libelle_nature": r.get("libelle_nature"),
        }
    return out


def effectifs_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    out = {}
    for r in rows:
        out[(r["numero_college"], _year(r["rentree_scolaire"]))] = {
            "effectif_total": _int(r.get("nombre_eleves_total")),
            "effectif_6e": _int(r.get("nombre_total_de_6emes")),
            "effectif_5e": _int(r.get("nombre_total_de_5emes")),
            "effectif_4e": _int(r.get("nombre_total_de_4emes")),
            "effectif_3e": _int(r.get("nombre_total_de_3emes")),
            "effectif_segpa": _int(r.get("nombre_d_eleves_total_segpa")),
            "effectif_ulis": _int(r.get("nombre_d_eleves_total_ulis")),
            "rep": _int(r.get("rep")),
            # The dataset calls the REP+ flag "rep0".
            "rep_plus": _int(r.get("rep0")),
        }
    return out


def ips_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    order = {ds: i for i, ds in enumerate(IPS_DATASETS)}
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: order.get(r.get("_dataset"), -1)):
        if r.get("ips") is None:
            continue
        out[(r["uai"], _year(r["rentree_scolaire"]))] = {
            "ips": _float(r["ips"]),
            "ips_ecart_type": _float(r.get("ecart_type_de_l_ips")),
        }
    return out


def dnb_by_key(
    dnb_rows: list[dict[str, Any]], ivac_rows: list[dict[str, Any]]
) -> dict[tuple[str, int], dict[str, Any]]:
    """Results keyed by (uai, session). IVAC (with value added) wins over the raw DNB file."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for r in dnb_rows:
        presents = _int(r.get("presents"))
        admis = _int(r.get("admis"))
        sans_mention = _int(r.get("admis_sans_mention"))
        mention = (
            round(100 * (admis - sans_mention) / presents, 1)
            if presents and admis is not None and sans_mention is not None
            else None
        )
        out[(r["numero_d_etablissement"], _year(r["session"]))] = {
            "dnb_candidats": presents,
            "dnb_taux_reussite": _percent(r.get("taux_de_reussite")),
            "dnb_taux_mention": mention,
            "dnb_valeur_ajoutee": None,
            "source_dnb": "dnb-par-etablissement",
        }
    for r in ivac_rows:
        candidats = _int(r.get("nb_candidats_g"))
        mentions = _int(r.get("nb_mentions_global_g"))
        out[(r["uai"], _year(r["session"]))] = {
            "dnb_candidats": candidats,
            "dnb_taux_reussite": _percent(r.get("taux_de_reussite_g")),
            "dnb_taux_mention": round(100 * mentions / candidats, 1)
            if candidats and mentions is not None
            else None,
            "dnb_valeur_ajoutee": _float(r.get("va_du_taux_de_reussite_g")),
            "source_dnb": "ivac",
        }
    return out


EMPTY_EFFECTIFS = dict.fromkeys(
    ["effectif_total", "effectif_6e", "effectif_5e", "effectif_4e", "effectif_3e",
     "effectif_segpa", "effectif_ulis", "rep", "rep_plus"]
)
EMPTY_DNB = dict.fromkeys(
    ["dnb_candidats", "dnb_taux_reussite", "dnb_taux_mention", "dnb_valeur_ajoutee", "source_dnb"]
)


def build_history(
    schools: list[dict[str, Any]],
    raw: dict[str, list[dict[str, Any]]],
    first_year: int,
) -> list[dict[str, Any]]:
    """One row per school and school year, from ``first_year`` (or the school's first
    year with data) to the latest year with data.

    ``schools`` holds the identity columns (uai, nom, commune, code_insee, statut, lat,
    lon, secteur_id) that are repeated on every row.
    """
    effectifs = effectifs_by_key(raw["effectifs"])
    ips = ips_by_key(raw["ips"])
    dnb = dnb_by_key(raw["dnb"], raw["ivac"])
    years_with_data = {y for _, y in effectifs} | {y for _, y in ips}
    if not years_with_data:
        raise ValueError("No enrolment or IPS data returned for the schools")
    last_year = max(years_with_data)

    rows = []
    for school in schools:
        uai = school["uai"]
        # Start at the school's first year with any data (new schools have no earlier rows).
        years = [
            y for y in range(first_year, last_year + 1)
            if (uai, y) in effectifs or (uai, y) in ips or (uai, y + 1) in dnb
        ]
        if not years:
            continue
        for year in range(min(years), last_year + 1):
            session = year + 1  # the DNB of school year 2016-2017 is the 2017 session
            rows.append(
                {
                    **school,
                    "rentree": year,
                    "annee_scolaire": f"{year}-{year + 1}",
                    **effectifs.get((uai, year), EMPTY_EFFECTIFS),
                    **ips.get((uai, year), {"ips": None, "ips_ecart_type": None}),
                    "session_dnb": session,
                    **dnb.get((uai, session), EMPTY_DNB),
                }
            )
    return rows
