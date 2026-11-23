"""CSV exports that open correctly in French Excel, and the labels shared with the PDF.

French Excel expects: ";" as separator, a decimal comma, dd/mm/yyyy dates, and a UTF-8
byte-order mark (without it, accents are garbled). Text cells that start with = + - @ are
prefixed with ' so a spreadsheet never runs them as a formula (action titles are typed by
users: "CSV injection").
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

ALEA_LABELS = {
    "inondation": "Inondation",
    "argiles": "Argiles",
    "mouvements_terrain": "Mouvements de terrain",
    "industriel": "Industriel",
    "tmd": "Transport de matières dangereuses",
    "nucleaire": "Nucléaire",
    "general": "Général",
}
NIVEAU_LABELS = {"faible": "Faible", "moyen": "Modéré", "eleve": "Élevé"}
STATUT_LABELS = {"a_faire": "À faire", "en_cours": "En cours", "termine": "Terminée", "annule": "Annulée"}
PRIORITE_LABELS = {"basse": "Basse", "normale": "Normale", "haute": "Haute", "critique": "Critique"}
PPMS_LABELS = {"a_jour": "À jour", "ancien": "À mettre à jour", "absent": "Non renseigné"}


def label(mapping: dict[str, str], value: Any) -> str:
    key = getattr(value, "value", value)
    return mapping.get(key, "" if key is None else str(key))


def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def to_csv(header: list[str], rows: Iterable[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([cell(v) for v in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


HAZARDS = ["inondation", "argiles", "mouvements_terrain", "industriel", "tmd", "nucleaire"]


def colleges_csv(rows: list[dict[str, Any]]) -> bytes:
    """`rows`: school summaries with `profil` (risk profile) and `ppms` (PpmsStatus)."""
    header = [
        "UAI", "Nom", "Commune", "Statut", "Secteur", "Élèves", "IPS",
        "Score global", "Niveau", "Aléa principal", "Indice de priorité",
        *[f"Score {ALEA_LABELS[h].lower()}" for h in HAZARDS],
        "PPMS", "PPMS mis à jour le", "Alertes",
    ]
    return to_csv(header, (
        [
            r["uai"], r["nom"], r["commune"], r["statut"], r["secteur_id"], r["effectif_total"], r["ips"],
            r["score_global"], label(NIVEAU_LABELS, r["niveau"]), label(ALEA_LABELS, r["alea_principal"]),
            r["indice_priorite"],
            *[r["profil"]["aleas"][h] for h in HAZARDS],
            label(PPMS_LABELS, r["ppms"].statut), r["ppms"].derniere_mise_a_jour,
            " | ".join(r["alertes"]),
        ]
        for r in rows
    ))


def actions_csv(actions: list[Any], school_names: dict[str, str]) -> bytes:
    header = [
        "ID", "UAI", "Collège", "Titre", "Description", "Aléa", "Priorité", "Statut",
        "Responsable", "Échéance", "En retard", "Créée le", "Modifiée le",
    ]
    return to_csv(header, (
        [
            a.id, a.uai, school_names.get(a.uai, ""), a.titre, a.description,
            label(ALEA_LABELS, a.alea), label(PRIORITE_LABELS, a.priorite), label(STATUT_LABELS, a.statut),
            a.responsable, a.echeance, a.en_retard, a.created_at, a.updated_at,
        ]
        for a in actions
    ))
