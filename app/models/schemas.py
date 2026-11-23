"""Response schemas for the read-only reference data."""
from __future__ import annotations

from pydantic import BaseModel


class RiskProfile(BaseModel):
    score_global: float
    niveau: str
    alea_principal: str
    aleas: dict[str, float]
    alertes: list[str]


class CollegeSummary(BaseModel):
    uai: str
    nom: str
    commune: str
    statut: str
    lat: float
    lon: float
    secteur_id: str | None
    effectif_total: int | None
    ips: float | None
    score_global: float
    niveau: str
    alea_principal: str


class CollegeNearby(CollegeSummary):
    distance_km: float


class CollegeDetail(BaseModel):
    uai: str
    nom: str
    commune: str
    code_insee: str
    statut: str
    lat: float
    lon: float
    secteur_id: str | None
    effectif_total: int | None
    ips: float | None
    argiles_classe: str | None
    dans_ppri: bool
    ppri: str | None
    nb_cavites_500m: int
    nb_mouvements_terrain_500m: int
    distance_icpe_km: float | None
    distance_seveso_km: float | None
    nb_icpe_1km: int
    distance_tmd_m: int
    type_tmd_proche: str | None
    dans_zone_tmd_500m: bool
    distance_centrale_km: float | None
    centrale_proche: str | None
    distance_repere_crue_km: float | None
    nb_catnat: int
    nb_catnat_inondations: int
    nb_catnat_secheresse: int
    nb_catnat_mouvement_terrain: int
    annee_dicrim: int | None
    profil_risque: RiskProfile


class YearRecord(BaseModel):
    rentree: int
    annee_scolaire: str
    effectif_total: int | None
    effectif_6e: int | None
    effectif_5e: int | None
    effectif_4e: int | None
    effectif_3e: int | None
    effectif_segpa: int | None
    effectif_ulis: int | None
    rep: int | None
    rep_plus: int | None
    ips: float | None
    ips_ecart_type: float | None
    session_dnb: int
    dnb_candidats: int | None
    dnb_taux_reussite: float | None
    dnb_taux_mention: float | None
    dnb_valeur_ajoutee: float | None
