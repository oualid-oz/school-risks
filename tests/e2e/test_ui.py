"""The interface, end to end in a real browser (see conftest.py for the setup)."""
import json
import re
from datetime import date

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import ADMIN, login

SCHOOL = ("0450003J", 48.08018, 1.87575)  # Jean Moulin, Artenay
OTHER = "0450004K"
AXE = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"


# ---- dashboard and map ---------------------------------------------------------------
def test_dashboard_and_map_fit_on_first_load(admin_page):
    page = admin_page
    expect(page.locator("#stats-grid .stat-card")).to_have_count(4)
    expect(page.locator("#stats-grid")).to_contain_text("PPMS à jour")
    expect(page.locator("#school-list .school-row")).to_have_count(8)
    assert page.evaluate("schoolMarkers.length") == 70
    # Regression (Sprint 5): the map was fitted while hidden and showed max zoom.
    zoom = page.evaluate("map.getZoom()")
    assert 7 <= zoom <= 11, zoom


def test_ranking_toggle(admin_page):
    page = admin_page
    first_by_score = page.locator("#school-list .school-row strong").first.inner_text()
    page.get_by_role("button", name="Élèves exposés").click()
    expect(page.get_by_role("button", name="Élèves exposés")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#legend-size")).to_be_visible()
    expect(page.locator("#ranking-caption")).to_contain_text("élèves exposés")
    expect(page.locator("#school-list .school-row").first).to_contain_text("indice")
    assert page.locator("#school-list .school-row strong").first.inner_text() == "Sainte Croix Saint Euverte"
    page.get_by_role("button", name="Score de risque").click()
    assert page.locator("#school-list .school-row strong").first.inner_text() == first_by_score


def _click_map_near(page, lat, lon, dx=15, dy=15):
    page.evaluate(f"map.setView([{lat}, {lon}], 14, {{animate: false}})")
    point = page.evaluate(f"map.latLngToContainerPoint([{lat}, {lon}])")
    page.locator("#map").click(position={"x": point["x"] + dx, "y": point["y"] + dy})


def test_click_on_map_shows_sector_and_nearby_schools(admin_page):
    page = admin_page
    uai, lat, lon = SCHOOL
    _click_map_near(page, lat, lon)
    panel = page.locator("#point-panel")
    expect(panel).to_contain_text("Secteur de collège")
    expect(panel.locator(".point-block").first).to_contain_text("Jean Moulin")
    expect(panel.locator(".point-block").nth(1)).to_contain_text("km")
    panel.locator(".point-block").first.get_by_role("button", name="Jean Moulin").click()
    expect(page).to_have_url(re.compile(f"#school/{uai}$"))
    expect(page.locator("#school-page h1")).to_have_text("Jean Moulin")


def test_address_search_uses_the_national_address_base(admin_page):
    page = admin_page
    _, lat, lon = SCHOOL
    answer = {"features": [
        {"geometry": {"coordinates": [lon + 0.001, lat]},
         "properties": {"label": "1 Rue de l’Exemple 45410 Artenay", "postcode": "45410"}},
        {"geometry": {"coordinates": [2.35, 48.85]},
         "properties": {"label": "1 Rue de l’Exemple 75001 Paris", "postcode": "75001"}},
    ]}
    seen = []

    def fake_ban(route):
        seen.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answer))

    page.route("https://api-adresse.data.gouv.fr/**", fake_ban)
    page.fill("#address-input", "1 rue de l'exemple artenay")
    page.get_by_role("button", name="Chercher").click()
    options = page.locator("#address-results button")
    expect(options).to_have_count(1)  # Paris filtered out: Loiret only
    options.first.click()
    expect(page.locator("#point-panel h3")).to_have_text("1 Rue de l’Exemple 45410 Artenay")
    expect(page.locator("#point-panel")).to_contain_text("Jean Moulin")
    assert "q=1%20rue" in seen[0]
    page.get_by_role("button", name="Effacer le point").click()
    expect(page.locator("#point-panel")).to_be_hidden()


# ---- school page: PPMS, actions, exports ---------------------------------------------
def test_ppms_forms(admin_page):
    page = admin_page
    page.goto(f"/#school/{OTHER}")
    panel = page.locator(".ppms-panel")
    expect(panel.locator(".ppms-badge")).to_have_text("Non renseigné")
    panel.locator("form", has_text="Mise à jour du PPMS").get_by_role("button", name="Enregistrer").click()
    expect(panel.locator(".ppms-history-list").first).to_contain_text("Mise à jour du")
    drill = panel.locator("form", has_text="Exercice réalisé")
    drill.locator("select").select_option("intrusion")
    drill.get_by_role("button", name="Enregistrer").click()
    expect(page.locator(".ppms-panel .ppms-badge")).to_have_text("À jour")
    expect(page.locator(".ppms-panel .ppms-drills")).to_contain_text(f"Intrusion : {date.today():%d/%m/%Y}")


def test_action_lifecycle(admin_page):
    page = admin_page
    page.goto(f"/#school/{SCHOOL[0]}")
    page.get_by_role("button", name="Nouvelle action").click()
    form = page.locator("#action-form")
    form.locator("[name=titre]").fill("Vérifier les kits de confinement")
    form.locator("[name=alea]").select_option("industriel")
    form.locator("[name=echeance]").fill("2026-01-15")
    form.get_by_role("button", name="Enregistrer").click()
    card = page.locator(".action-card", has_text="Vérifier les kits")
    expect(card).to_contain_text("En retard · 15/01/2026")
    card.get_by_role("button", name="Modifier").click()
    form.locator("[name=priorite]").select_option("critique")
    form.get_by_role("button", name="Enregistrer").click()
    expect(page.locator(".action-card", has_text="Vérifier les kits")).to_contain_text("Priorité critique")
    page.locator(".action-card", has_text="Vérifier les kits").get_by_role("button", name="Terminer").click()
    expect(page.locator(".closed-actions summary")).to_contain_text("(1)")
    page.locator(".closed-actions summary").click()
    page.locator(".closed-actions .action-card").get_by_role("button", name="Supprimer").click()
    expect(page.locator(".action-card", has_text="Vérifier les kits")).to_have_count(0)


def test_action_form_shows_api_errors(admin_page):
    page = admin_page
    page.goto(f"/#school/{SCHOOL[0]}")
    page.get_by_role("button", name="Nouvelle action").click()
    form = page.locator("#action-form")
    form.locator("[name=titre]").fill("ab")  # the browser checks minlength first
    form.get_by_role("button", name="Enregistrer").click()
    expect(page.locator("#action-dialog")).to_be_visible()
    page.locator("#action-dialog [data-close]").first.click()


@pytest.mark.parametrize(("button", "suffix"), [("Fiche PDF", ".pdf"), ("Actions (CSV)", ".csv")])
def test_exports_download(admin_page, button, suffix):
    page = admin_page
    page.goto(f"/#school/{SCHOOL[0]}")
    with page.expect_download() as info:
        page.get_by_role("button", name=button).click()
    download = info.value
    assert download.suggested_filename.endswith(suffix) and SCHOOL[0] in download.suggested_filename
    content = open(download.path(), "rb").read()
    assert content.startswith(b"%PDF-" if suffix == ".pdf" else b"\xef\xbb\xbf")


def test_dashboard_csv_export(admin_page):
    with admin_page.expect_download() as info:
        admin_page.get_by_role("button", name="Exporter (CSV)").click()
    assert info.value.suggested_filename.startswith("colleges_")


# ---- accounts ------------------------------------------------------------------------
def test_account_dialog_alerts_and_password(page, make_user):
    username, password = make_user("referent", [SCHOOL[0]])
    login(page, username, password)
    page.get_by_role("button", name="Mon compte").click()
    alerts = page.locator("#alerts-form")
    alerts.locator("#alerts-email").fill("ref@example.fr")
    alerts.locator("#alerts-enabled").check()
    alerts.get_by_role("button", name="Enregistrer les alertes").click()
    expect(alerts.locator(".form-message")).to_have_text("Alertes activées.")

    pw = page.locator("#password-form")
    pw.locator("[name=current]").fill("wrong-password")
    pw.locator("[name=new]").fill(password + "-new")
    pw.locator("[name=confirm]").fill(password + "-new")
    pw.get_by_role("button", name="Changer le mot de passe").click()
    expect(pw.locator(".form-message")).to_have_text("Mot de passe actuel incorrect")
    pw.locator("[name=current]").fill(password)
    pw.get_by_role("button", name="Changer le mot de passe").click()
    expect(pw.locator(".form-message")).to_contain_text("Mot de passe modifié")
    page.locator("#account-dialog [data-close]").click()
    page.goto(f"/#school/{OTHER}")  # still signed in with the new token
    expect(page.locator(".ppms-panel")).to_be_visible()


def test_readers_have_no_alerts_section(page, make_user):
    login(page, *make_user("lecteur"))
    page.get_by_role("button", name="Mon compte").click()
    expect(page.locator("#alerts-form")).to_be_hidden()
    expect(page.locator("#password-form")).to_be_visible()


def test_admin_creates_a_referent_who_only_edits_their_school(page):
    login(page, *ADMIN)
    page.get_by_role("link", name="Utilisateurs").click()
    page.get_by_role("button", name="Nouvel utilisateur").click()
    form = page.locator("#user-form")
    form.locator("[name=username]").fill("ui-referent")
    form.locator("[name=role]").select_option("referent")
    form.locator("[name=password]").fill("ui-referent-password")
    form.locator("[data-school-filter]").fill("artenay")
    form.locator(f".school-option input[value='{SCHOOL[0]}']").check()
    form.get_by_role("button", name="Enregistrer").click()
    expect(page.locator("#user-table tr", has_text="ui-referent")).to_contain_text("Référent")

    page.get_by_role("button", name="Déconnexion").click()
    login(page, "ui-referent", "ui-referent-password")
    expect(page.get_by_role("link", name="Utilisateurs")).to_be_hidden()
    page.goto(f"/#school/{SCHOOL[0]}")
    expect(page.get_by_role("button", name="Nouvelle action")).to_be_visible()
    page.goto(f"/#school/{OTHER}")
    expect(page.locator(".actions-panel")).to_contain_text("visibles par ses référents")
    expect(page.get_by_role("button", name="Nouvelle action")).to_have_count(0)
    page.goto("/#admin")
    expect(page.locator("#dashboard")).to_be_visible()


def test_admin_cannot_demote_themselves(admin_page):
    page = admin_page
    page.get_by_role("link", name="Utilisateurs").click()
    page.locator("#user-table tr", has_text="(vous)").get_by_role("button", name="Modifier").click()
    page.locator("#user-form [name=role]").select_option("lecteur")
    page.locator("#user-form").get_by_role("button", name="Enregistrer").click()
    expect(page.locator("#user-form .form-message")).to_contain_text("Vous ne pouvez pas")


# ---- accessibility -----------------------------------------------------------------------
def _axe(page):
    page.add_script_tag(url=AXE)
    result = page.evaluate("axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']})")
    return [
        f"{v['impact']} {v['id']}: {v['help']} → {[n['target'] for n in v['nodes'][:5]]}"
        for v in result["violations"]
        if v["impact"] in ("serious", "critical")
    ]


def test_accessibility(page):
    page.goto("/")
    problems = {"login": _axe(page)}
    login(page, *ADMIN)
    problems["dashboard"] = _axe(page)
    page.goto(f"/#school/{SCHOOL[0]}")
    expect(page.locator(".ppms-panel")).to_be_visible()
    problems["school"] = _axe(page)
    page.goto("/#admin")
    expect(page.locator("#user-table table")).to_be_visible()
    problems["admin"] = _axe(page)
    page.goto("/#simulation")
    expect(page.locator("#simulation-top tbody tr").first).to_be_visible()
    problems["simulation"] = _axe(page)
    page.get_by_role("button", name="Mon compte").click()
    problems["account"] = _axe(page)
    assert not any(problems.values()), json.dumps(problems, indent=1, ensure_ascii=False)


def test_keyboard_only(page):
    """Sign in, open a school and come back without a mouse."""
    page.goto("/")
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.locator("#username").focus()
    page.keyboard.type(ADMIN[0])
    page.keyboard.press("Tab")
    page.keyboard.type(ADMIN[1])
    page.keyboard.press("Enter")
    first = page.locator("#school-list .school-row").first
    expect(first).to_be_visible()
    first.focus()
    page.keyboard.press("Enter")
    # The focus follows the navigation: on the school page's title.
    expect(page.locator("#school-page h1")).to_be_focused()
    url = page.url
    page.locator(".skip-link").focus()  # first Tab stop of every page (checked on the login page)
    page.keyboard.press("Enter")  # skip link: to the title, URL (= the router) unchanged
    expect(page.locator("#school-page h1")).to_be_focused()
    assert page.url == url
    page.get_by_role("button", name="Mon compte").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#account-dialog")).to_be_visible()
    page.keyboard.press("Escape")  # native <dialog> closes on Escape
    expect(page.locator("#account-dialog")).to_be_hidden()


def test_account_menu_is_a_dropdown_on_phones(admin_page):
    page = admin_page
    page.set_viewport_size({"width": 390, "height": 844})
    toggle = page.get_by_role("button", name="Menu du compte")
    menu = page.locator("#account-menu")
    expect(toggle).to_be_visible()
    expect(menu).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(menu).to_be_visible()
    expect(menu).to_contain_text("e2e-admin · admin")
    page.keyboard.press("Escape")  # closes, focus back on the button
    expect(menu).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.click()
    page.get_by_role("link", name="Utilisateurs").click()  # choosing an item closes the menu
    expect(page.locator("#admin-page")).to_be_visible()
    expect(menu).to_be_hidden()
    toggle.click()
    page.mouse.click(20, 700)  # a click elsewhere (outside the open menu) closes it
    expect(menu).to_be_hidden()
    page.set_viewport_size({"width": 1280, "height": 900})  # desktop: inline, no toggle
    expect(toggle).to_be_hidden()
    expect(page.get_by_role("button", name="Mon compte")).to_be_visible()


def test_simulation_page(admin_page):
    page = admin_page
    page.get_by_role("link", name="Simulation").click()
    expect(page.locator("#simulation-page h1")).to_have_text("Simulation")
    summary = page.locator("#simulation-summary")
    expect(summary.locator(".sim-card").first).to_contain_text("0")  # today's values: no change
    expect(page.locator("#simulation-top tbody tr")).to_have_count(10)
    expect(page.locator("#simulation-page")).not_to_contain_text("SRT_")  # no technical settings shown

    page.get_by_label("Élevé à partir de (valeur)").fill("40")
    expect(page.locator("#simulation-changes tbody tr").first).to_be_visible()
    assert int(summary.locator(".sim-card strong").first.inner_text()) > 0
    expect(page.locator("#simulation-changes")).to_contain_text("→")

    page.get_by_role("button", name="Revenir aux valeurs actuelles").click()
    expect(page.get_by_label("Élevé à partir de (valeur)")).to_have_value("50")
    expect(page.locator("#simulation-changes")).to_contain_text("Aucun collège ne change de niveau")
    assert page.evaluate("thresholds.eleve") == 50  # the real thresholds are untouched


def test_action_history_comments_and_assignment(admin_page, make_user):
    page = admin_page
    referent, _ = make_user("referent", [SCHOOL[0]])
    page.goto(f"/#school/{SCHOOL[0]}")
    page.get_by_role("button", name="Nouvelle action").click()
    form = page.locator("#action-form")
    form.locator("[name=titre]").fill("Commander les kits PPMS")
    form.locator("[name=echeance]").fill("2026-01-10")
    expect(form.locator("[name=responsable_user_id] option", has_text=referent)).to_have_count(1)
    form.locator("[name=responsable_user_id]").select_option(label=f"{referent} (référent)")
    form.get_by_role("button", name="Enregistrer").click()
    card = page.locator(".action-card", has_text="Commander les kits PPMS")
    expect(card).to_contain_text(f"Assignée à {referent}")

    card.get_by_role("button", name="Modifier").click()
    form.locator("[name=statut]").select_option("en_cours")
    form.get_by_role("button", name="Enregistrer").click()

    kits = page.locator(".action-card", has_text="Commander les kits PPMS")
    kits.get_by_role("button", name="Historique").click()
    dialog = page.locator("#history-dialog")
    expect(dialog.locator(".history-item")).to_have_count(2)
    expect(dialog.locator(".history-modification")).to_contain_text("Statut : À faire → En cours")
    expect(dialog.locator(".history-creation")).to_contain_text("a créé l’action")
    dialog.locator("[name=texte]").fill("Fournisseur en rupture, livraison le 10/12.")
    dialog.get_by_role("button", name="Commenter").click()
    expect(dialog.locator(".history-comment")).to_have_text("Fournisseur en rupture, livraison le 10/12.")
    page.keyboard.press("Escape")  # closing refreshes the page: the card shows the comment
    expect(kits.locator(".last-comment")).to_contain_text("Fournisseur en rupture")

    page.get_by_role("button", name="Déconnexion").click()
    login(page, *make_user("lecteur"))
    page.goto(f"/#school/{SCHOOL[0]}")
    kits.get_by_role("button", name="Historique").click()
    expect(page.locator("#history-dialog .history-item")).to_have_count(3)
    expect(page.locator("#comment-form")).to_be_hidden()  # readers read only
    expect(page.locator("#history-dialog").get_by_role("button", name="Supprimer")).to_have_count(0)
