"""Column names and dtypes of the two reference tables (files and PostGIS alike)."""

RISQUES_SCHEMA: dict[str, str] = {
    "uai": "str", "nom": "str", "commune": "str", "code_insee": "str", "statut": "str",
    "lat": "float64", "lon": "float64", "secteur_id": "str", "effectif_total": "Int64",
    "ips": "float64", "argiles_classe": "str", "dans_ppri": "bool", "ppri": "str",
    "nb_cavites_500m": "int64", "nb_mouvements_terrain_500m": "int64",
    "distance_icpe_km": "float64", "distance_seveso_km": "float64", "nb_icpe_1km": "int64",
    "distance_tmd_m": "int64", "type_tmd_proche": "str", "dans_zone_tmd_500m": "bool",
    "distance_centrale_km": "float64", "centrale_proche": "str",
    "distance_repere_crue_km": "float64", "nb_catnat": "int64",
    "nb_catnat_inondations": "int64", "nb_catnat_secheresse": "int64",
    "nb_catnat_mouvement_terrain": "int64", "annee_dicrim": "Int64",
}

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
