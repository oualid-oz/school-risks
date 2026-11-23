"""Reference data tables (schools, school years, sectors), PostGIS only.

They live in their own MetaData so SQLite (tests, quick local runs) never tries to create
geometry columns: on SQLite the data store keeps reading the parquet / GeoJSON files.
Columns mirror ``app.pipeline.build.RISQUES_SCHEMA`` and ``HISTORY_SCHEMA``.
"""
from __future__ import annotations

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)

reference_metadata = MetaData()

college = Table(
    "college",
    reference_metadata,
    Column("uai", String(8), primary_key=True),
    Column("nom", String, nullable=False),
    Column("commune", String, nullable=False),
    Column("code_insee", String(5), nullable=False),
    Column("statut", String, nullable=False),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("secteur_id", String),
    Column("effectif_total", Integer),
    Column("ips", Float),
    Column("argiles_classe", String, nullable=False),
    Column("dans_ppri", Boolean, nullable=False),
    Column("ppri", String, nullable=False),
    Column("nb_cavites_500m", Integer, nullable=False),
    Column("nb_mouvements_terrain_500m", Integer, nullable=False),
    Column("distance_icpe_km", Float),
    Column("distance_seveso_km", Float),
    Column("nb_icpe_1km", Integer, nullable=False),
    Column("distance_tmd_m", Integer, nullable=False),
    Column("type_tmd_proche", String),
    Column("dans_zone_tmd_500m", Boolean, nullable=False),
    Column("distance_centrale_km", Float, nullable=False),
    Column("centrale_proche", String),
    Column("distance_repere_crue_km", Float, nullable=False),
    Column("nb_catnat", Integer, nullable=False),
    Column("nb_catnat_inondations", Integer, nullable=False),
    Column("nb_catnat_secheresse", Integer, nullable=False),
    Column("nb_catnat_mouvement_terrain", Integer, nullable=False),
    Column("annee_dicrim", Integer),
    Column("geom", Geometry("POINT", srid=4326, spatial_index=True), nullable=False),
)

college_annee = Table(
    "college_annee",
    reference_metadata,
    Column("uai", String(8), ForeignKey("college.uai", ondelete="CASCADE"), primary_key=True),
    Column("rentree", Integer, primary_key=True),
    Column("nom", String, nullable=False),
    Column("commune", String, nullable=False),
    Column("code_insee", String(5), nullable=False),
    Column("statut", String, nullable=False),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("secteur_id", String),
    Column("annee_scolaire", String(9), nullable=False),
    Column("effectif_total", Integer),
    Column("effectif_6e", Integer),
    Column("effectif_5e", Integer),
    Column("effectif_4e", Integer),
    Column("effectif_3e", Integer),
    Column("effectif_segpa", Integer),
    Column("effectif_ulis", Integer),
    Column("rep", Integer),
    Column("rep_plus", Integer),
    Column("ips", Float),
    Column("ips_ecart_type", Float),
    Column("session_dnb", Integer, nullable=False),
    Column("dnb_candidats", Integer),
    Column("dnb_taux_reussite", Float),
    Column("dnb_taux_mention", Float),
    Column("dnb_valeur_ajoutee", Float),
    Column("source_dnb", String),
)

secteur = Table(
    "secteur",
    reference_metadata,
    Column("secteur_id", String, primary_key=True),
    Column("secteur", String),
    Column("commune", String),
    Column("code_insee", String(5)),
    Column("departement", String),
    Column("geom", Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False),
)
