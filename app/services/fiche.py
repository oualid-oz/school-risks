"""PDF "fiche risques" for one school (fpdf2: pure Python, so it also runs on Vercel).

DejaVu Sans is bundled in app/assets/fonts because the PDF core fonts only cover Latin-1
and the text typed by users (action titles, comments) can contain any character.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.fonts import FontFace

from app.core.config import settings
from app.models.ppms import PpmsStatus
from app.services.exports import (
    ALEA_LABELS,
    HAZARDS,
    NIVEAU_LABELS,
    PPMS_LABELS,
    PRIORITE_LABELS,
    STATUT_LABELS,
    label,
)
from app.services.scoring import niveau

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
GREEN, INK, MUTED, LINE = (24, 118, 83), (23, 35, 30), (110, 120, 114), (226, 231, 226)
LEVEL_COLORS = {"faible": (84, 168, 121), "moyen": (229, 167, 73), "eleve": (214, 91, 80)}
DRILL_LABELS = {"risques_majeurs": "Risques majeurs", "intrusion": "Intrusion", "incendie": "Incendie"}
MAX_ACTIONS = 25


def level_of(score: float) -> str:
    return niveau(score)  # same thresholds as the scores (settings)


def fr_date(day: date | None) -> str:
    return day.strftime("%d/%m/%Y") if day else "—"


def number(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        text = f"{value:,.{digits}f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:,}" if isinstance(value, int) else str(value)
    # French: 7 640,5 (narrow no-break space for thousands, comma for decimals)
    return text.replace(",", "\u202f").replace(".", ",") + unit


@dataclass
class FicheData:
    college: dict[str, Any]  # reference row + profil_risque (alerts already include PPMS)
    ppms: PpmsStatus
    actions: list[Any]  # open actions (ActionRead)
    snapshots: list[Any]  # ScoreSnapshotRead, oldest first
    generated_on: date


class FichePDF(FPDF):
    def __init__(self, title: str) -> None:
        super().__init__(format="A4", unit="mm")
        self.add_font("DejaVu", "", str(FONTS / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONTS / "DejaVuSans-Bold.ttf"))
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(auto=True, margin=18)
        self.fiche_title = title

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 4, "Indicateurs de priorisation, pas une classification officielle. "
                        f"School Risk Tracker {settings.build_version}", align="L")
        self.cell(0, 4, f"{self.fiche_title} · page {self.page_no()}/{{nb}}", align="R")

    # ---- building blocks -------------------------------------------------------------
    def section(self, title: str) -> None:
        if self.get_y() > self.h - 42:
            self.add_page()
        self.ln(2.5)
        self.set_font("DejaVu", "B", 10.5)
        self.set_text_color(*GREEN)
        self.cell(0, 7, title.upper(), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_text_color(*INK)

    def key_values(self, pairs: list[tuple[str, str]], columns: int = 2) -> None:
        width = (self.w - self.l_margin - self.r_margin) / columns
        for start in range(0, len(pairs), columns):
            y = self.get_y()
            for i, (key, value) in enumerate(pairs[start:start + columns]):
                self.set_xy(self.l_margin + i * width, y)
                self.set_font("DejaVu", "", 7.5)
                self.set_text_color(*MUTED)
                self.cell(width, 4, key, new_x="LEFT", new_y="NEXT")
                self.set_font("DejaVu", "", 9.5)
                self.set_text_color(*INK)
                self.multi_cell(width - 3, 4.6, value)
            self.set_y(max(self.get_y(), y + 9))

    def bar(self, name: str, score: float) -> None:
        label_w, height = 62, 4.2
        bar_w = self.w - self.l_margin - self.r_margin - label_w - 16
        y = self.get_y()
        self.set_font("DejaVu", "", 8.5)
        self.cell(label_w, height, name)
        self.set_fill_color(240, 243, 240)
        self.rect(self.l_margin + label_w, y + 0.6, bar_w, height - 1.2, style="F")
        self.set_fill_color(*LEVEL_COLORS[level_of(score)])
        if score > 0:
            filled = bar_w * min(score, 100) / 100
            self.rect(self.l_margin + label_w, y + 0.6, filled, height - 1.2, style="F")
        self.set_xy(self.l_margin + label_w + bar_w + 2, y)
        self.cell(14, height, number(score), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(1.2)

    def bullet_list(self, items: list[str], color: tuple[int, int, int] = INK) -> None:
        self.set_font("DejaVu", "", 9)
        self.set_text_color(*color)
        for item in items:
            self.multi_cell(0, 4.8, f"•  {item}", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*INK)

    def muted(self, text: str) -> None:
        self.set_font("DejaVu", "", 8.5)
        self.set_text_color(*MUTED)
        self.multi_cell(0, 4.6, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*INK)

    def grid(self, header: list[str], rows: list[list[str]], widths: tuple[int, ...]) -> None:
        self.set_font("DejaVu", "", 8)
        self.set_fill_color(255, 255, 255)  # do not inherit the colour of the last bar
        self.set_draw_color(*LINE)
        head = FontFace(emphasis="BOLD", color=INK, fill_color=(240, 243, 240))
        with self.table(col_widths=widths, headings_style=head, line_height=4.6, cell_fill_mode="NONE",
                        borders_layout="HORIZONTAL_LINES", text_align="LEFT") as table:
            table.row(header)
            for values in rows:
                table.row(values)


def build_fiche(data: FicheData) -> bytes:
    c, profile, ppms = data.college, data.college["profil_risque"], data.ppms
    pdf = FichePDF(f"Fiche risques {c['uai']}")
    pdf.set_title(f"Fiche risques — {c['nom']} ({c['uai']})")
    pdf.set_author("School Risk Tracker")
    pdf.set_subject("Risques naturels et technologiques, collèges du Loiret")
    pdf.add_page()

    # Header
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*GREEN)
    pdf.cell(0, 5, "FICHE RISQUES · COLLÈGE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("DejaVu", "B", 18)
    pdf.multi_cell(0, 8.5, c["nom"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9.5)
    pdf.set_text_color(*MUTED)
    subtitle = f"{c['commune']} · {c['statut']} · UAI {c['uai']} · éditée le {fr_date(data.generated_on)}"
    pdf.cell(0, 5.5, subtitle, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)

    # Score
    pdf.section("Synthèse")
    niveau = profile["niveau"]
    y = pdf.get_y()
    pdf.set_fill_color(*LEVEL_COLORS[niveau])
    pdf.rect(pdf.l_margin, y, 38, 20, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(pdf.l_margin, y + 2)
    pdf.set_font("DejaVu", "B", 20)
    pdf.cell(38, 9, number(profile["score_global"]), align="C", new_x="LEFT", new_y="NEXT")
    pdf.set_font("DejaVu", "", 8)
    pdf.cell(38, 5, f"/ 100 · {label(NIVEAU_LABELS, niveau)}", align="C")
    pdf.set_text_color(*INK)
    pdf.set_xy(pdf.l_margin + 44, y)
    pdf.set_left_margin(pdf.l_margin + 44)
    pdf.key_values([
        ("Aléa principal", label(ALEA_LABELS, profile["alea_principal"])),
        ("Indice de priorité (élèves × score / 100)", number(profile.get("indice_priorite"))),
        ("Élèves", number(c["effectif_total"])),
        ("IPS", number(c["ips"])),
    ])
    pdf.set_left_margin(16)
    pdf.set_y(max(pdf.get_y(), y + 22))

    pdf.section("Score par aléa (0–100)")
    for hazard in HAZARDS:
        pdf.bar(ALEA_LABELS[hazard], profile["aleas"][hazard])

    pdf.section("Données de référence")
    pdf.key_values([
        ("Plan de prévention inondation (PPRI)", c["ppri"] or ("Oui" if c["dans_ppri"] else "Non")),
        ("Retrait-gonflement des argiles", (c["argiles_classe"] or "—").capitalize()),
        ("Cavités / mouvements de terrain à 500 m",
         f"{c['nb_cavites_500m']} / {c['nb_mouvements_terrain_500m']}"),
        ("Site Seveso le plus proche", number(c["distance_seveso_km"], " km")),
        ("Installations classées à 1 km", number(c["nb_icpe_1km"])),
        ("Transport de matières dangereuses",
         f"{number(c['distance_tmd_m'], ' m', 0)} ({c['type_tmd_proche'] or '—'})"),
        ("Centrale nucléaire la plus proche",
         f"{c['centrale_proche'] or '—'}, {number(c['distance_centrale_km'], ' km')}"),
        ("Arrêtés CatNat (inondation / sécheresse / mvt)",
         f"{c['nb_catnat']} ({c['nb_catnat_inondations']} / {c['nb_catnat_secheresse']} / "
         f"{c['nb_catnat_mouvement_terrain']})"),
        ("DICRIM de la commune", str(c["annee_dicrim"]) if c["annee_dicrim"] else "Absent"),  # a year
        ("Repère de crue le plus proche", number(c["distance_repere_crue_km"], " km")),
    ])

    pdf.section("Alertes")
    if profile["alertes"]:
        pdf.bullet_list(profile["alertes"], color=(150, 90, 30))
    else:
        pdf.muted("Aucune alerte.")

    pdf.section(f"PPMS — année scolaire {ppms.annee_scolaire}")
    pdf.key_values([
        ("Statut", label(PPMS_LABELS, ppms.statut)),
        ("Dernière mise à jour", fr_date(ppms.derniere_mise_a_jour)),
        ("À revoir avant le", fr_date(ppms.prochaine_mise_a_jour)),
        ("Exercices cette année", " · ".join(
            f"{DRILL_LABELS[getattr(k, 'value', k)]} : {fr_date(v)}" for k, v in ppms.exercices_annee.items()
        )),
    ])

    pdf.section(f"Actions ouvertes ({len(data.actions)})")
    if data.actions:
        rows = [
            [a.titre, label(ALEA_LABELS, a.alea), label(PRIORITE_LABELS, a.priorite),
             fr_date(a.echeance) + (" (en retard)" if a.en_retard else ""), label(STATUT_LABELS, a.statut)]
            for a in data.actions[:MAX_ACTIONS]
        ]
        pdf.grid(["Action", "Aléa", "Priorité", "Échéance", "Statut"], rows, (64, 30, 18, 44, 22))
        if len(data.actions) > MAX_ACTIONS:
            pdf.muted(f"… et {len(data.actions) - MAX_ACTIONS} autre(s) : voir l’application.")
    else:
        pdf.muted("Aucune action ouverte.")

    pdf.section("Évolution du score (instantanés annuels)")
    if data.snapshots:
        pdf.grid(
            ["Année", "Score", "Niveau", "Aléa principal", "Indice de priorité"],
            [[str(s.annee), number(s.score_global), label(NIVEAU_LABELS, s.niveau),
              label(ALEA_LABELS, s.alea_principal), number(s.indice_priorite)] for s in data.snapshots],
            (20, 22, 26, 60, 44),
        )
    else:
        pdf.muted("Aucun instantané enregistré.")

    return bytes(pdf.output())
