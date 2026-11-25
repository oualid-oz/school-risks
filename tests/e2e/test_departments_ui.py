"""Sprint 17: several departments in the interface (pilot data, see fixtures/pilot)."""
from playwright.sync_api import expect

from tests.e2e.conftest import ADMIN, login


def test_department_selector_filters_everything(pilot_page):
    page = pilot_page
    select = page.locator("#territory-select")
    expect(page.locator("#territory-field")).to_be_visible()
    expect(select.locator("option")).to_have_count(4)  # all + 3 departments
    expect(page.locator("#map-count")).to_have_text("206 collèges")
    expect(page.locator("#territory-gaps")).to_contain_text("136 collège(s)")

    select.select_option("17")
    expect(page.locator("#territory-heading")).to_contain_text("Charente-Maritime (17)")
    expect(page.locator("#territory-heading")).to_contain_text("académie Poitiers")
    expect(page.locator("#map-count")).to_have_text("61 collèges")
    expect(page.locator("#stats-grid")).to_contain_text("/ 61")  # PPMS card: x / 61 schools
    assert page.evaluate("visibleSchools().every((s) => s.departement === '17')")
    assert page.evaluate("schoolMarkers.filter(({marker}) => schoolLayer.hasLayer(marker)).length") == 61

    page.locator("#school-list .school-row").first.click()
    expect(page.locator("#school-page .meta-chip", has_text="Charente-Maritime (17)")).to_be_visible()
    expect(page.locator(".gaps-box")).to_contain_text("le score est un minimum")
    page.get_by_role("button", name="Retour à la carte").click()

    login(page, *ADMIN)  # a reload signs out (the token lives in memory); the choice stays
    expect(page.locator("#territory-select")).to_have_value("17")
    page.locator("#territory-select").select_option("")
    expect(page.locator("#map-count")).to_have_text("206 collèges")


def test_loiret_schools_have_no_gap(pilot_page):
    page = pilot_page
    page.locator("#territory-select").select_option("45")
    expect(page.locator("#map-count")).to_have_text("70 collèges")
    expect(page.locator("#territory-gaps")).to_have_count(0)
    page.goto("/#school/0450003J")
    expect(page.locator(".gaps-box")).to_have_count(0)


def test_territorial_hazards_are_explained_on_the_school_page(pilot_page):
    page = pilot_page
    page.goto("/#school/0740001Z")  # Val d'Abondance (74): zone 4, avalanche plan
    box = page.locator(".territory-box")
    expect(box).to_contain_text("Aléas propres au territoire")
    expect(box).to_contain_text("Séisme : zone de sismicité 4 (moyenne) (score 75/100)")
    expect(box).to_contain_text("Avalanche : plan de prévention avalanche")
    expect(box).not_to_contain_text("Radon")  # class 1: not shown

    page.goto("/#school/0450003J")  # Loiret: none of these hazards
    expect(page.locator("#school-page h1")).to_be_visible()
    expect(page.locator(".territory-box")).to_have_count(0)

    page.goto("/#simulation")
    expect(page.locator('[data-group="seisme"] input[type="range"]')).to_have_count(5)
    expect(page.locator('[data-group="radon"] input[type="range"]')).to_have_count(3)
    expect(page.locator('[data-group="poids"]')).to_contain_text("Avalanche (là où il existe)")


def test_national_view_opens_a_department_with_its_sectors(pilot_page):
    page = pilot_page
    page.locator("#territory-select").select_option("")
    # National view: the departments, coloured by mean score; no sectors; canvas markers.
    page.wait_for_function("departmentLayer && map.hasLayer(departmentLayer)")
    assert page.evaluate("departmentLayer.getLayers().length") == 3
    assert page.evaluate("sectorLayer.getLayers().length") == 0
    assert page.evaluate("map.options.preferCanvas") is True
    expect(page.locator("#sector-source")).to_have_count(0)

    # A click on Haute-Savoie opens it: its sectors from the national school map.
    page.evaluate("""departmentLayer.eachLayer((layer) => {
        if (layer.feature.properties.code === '74') layer.fire('click'); })""")
    expect(page.locator("#territory-select")).to_have_value("74")
    expect(page.locator("#map-count")).to_have_text("75 collèges")
    page.wait_for_function("sectorLayer.getLayers().length > 0")
    assert page.evaluate("map.hasLayer(departmentLayer)") is False
    expect(page.locator("#sector-source")).to_contain_text("carte scolaire nationale")
    expect(page.locator("#sector-source")).to_contain_text("découpage approché")

    page.locator("#territory-select").select_option("45")
    # 6 collèges of neighbouring departments serve Loiret communes too: one feature each,
    # their Loiret part from the department's map and the rest from the school map.
    expect(page.locator("#sector-source")).to_have_text(
        "Secteurs (59) : 53 carte des secteurs publiée par le département ; "
        "6 carte du département et carte scolaire nationale.")
    page.wait_for_function("sectorLayer.getLayers().length === 59")

    page.locator("#territory-select").select_option("17")
    expect(page.locator("#sector-source")).to_contain_text("aucune source pour ce département")

    page.locator("#territory-select").select_option("")
    page.wait_for_function("map.hasLayer(departmentLayer) && sectorLayer.getLayers().length === 0")
