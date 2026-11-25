"""Column names and dtypes of the two reference tables (files and PostGIS alike)."""

RISQUES_SCHEMA: dict[str, str] = {
    "uai": "str", "nom": "str", "commune": "str", "code_insee": "str", "statut": "str",
    "departement": "str", "departement_nom": "str", "academie": "str",
    "lat": "float64", "lon": "float64", "secteur_id": "str", "effectif_total": "Int64",
    "ips": "float64", "argiles_classe": "str", "dans_ppri": "bool", "ppri": "str",
    "nb_cavites_500m": "int64", "nb_mouvements_terrain_500m": "int64",
    "distance_icpe_km": "float64", "distance_seveso_km": "float64", "nb_icpe_1km": "int64",
    # TMD and flood-marker distance have no public source: unknown outside the schools
    # first published (Loiret), hence nullable.
    "distance_tmd_m": "Int64", "type_tmd_proche": "str", "dans_zone_tmd_500m": "boolean",
    "distance_centrale_km": "float64", "centrale_proche": "str",
    "distance_repere_crue_km": "float64", "nb_catnat": "int64",
    "nb_catnat_inondations": "int64", "nb_catnat_secheresse": "int64",
    "nb_catnat_mouvement_terrain": "int64", "annee_dicrim": "Int64",
    # Hazards of part of the territory (app/pipeline/hazards.py). Nullable: files published
    # before Sprint 19 do not have them, and a source may not cover a commune.
    "zone_sismique": "Int64", "classe_radon": "Int64",
    "ddrm_feu_foret": "boolean", "ppr_feu_foret": "boolean",
    "ppr_submersion_marine": "boolean", "ppr_recul_trait_cote": "boolean", "nb_catnat_littoral": "Int64",
    "ddrm_avalanche": "boolean", "ppr_avalanche": "boolean", "nb_catnat_avalanche": "Int64",
}

TERRITORY_COLUMNS = (
    "zone_sismique", "classe_radon", "ddrm_feu_foret", "ppr_feu_foret", "ppr_submersion_marine",
    "ppr_recul_trait_cote", "nb_catnat_littoral", "ddrm_avalanche", "ppr_avalanche", "nb_catnat_avalanche",
)

HISTORY_SCHEMA: dict[str, str] = {
    "uai": "str", "nom": "str", "commune": "str", "code_insee": "str", "statut": "str",
    "lat": "float64", "lon": "float64", "secteur_id": "str", "rentree": "int64",
    "annee_scolaire": "str", "effectif_total": "Int64", "effectif_6e": "Int64",
    "effectif_5e": "Int64", "effectif_4e": "Int64", "effectif_3e": "Int64",
    "effectif_segpa": "Int64", "effectif_ulis": "Int64", "rep": "Int64", "rep_plus": "Int64",
    "ips": "float64", "ips_ecart_type": "float64", "session_dnb": "int64",
    "dnb_candidats": "Int64", "dnb_taux_reussite": "float64", "dnb_taux_mention": "float64",
    "dnb_valeur_ajoutee": "float64", "source_dnb": "str",
}
