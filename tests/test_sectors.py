"""Sprint 14: catchment sectors from the Département du Loiret (offline)."""
from app.pipeline import sectors


def _feature(uai, name, ring, **extra):
    props = {"clg_rne": uai, "clg_sec": name, "clg_ville": "SAINT JEAN DE LA RUELLE",
             "clg_insee": "45285", "clg_dep": "LOIRET", **extra}
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


SQUARE = [[1.0, 47.0], [1.1, 47.0], [1.1, 47.1], [1.0, 47.1], [1.0, 47.0]]
BIGGER = [[1.0, 47.0], [1.12, 47.0], [1.12, 47.1], [1.0, 47.1], [1.0, 47.0]]


def test_properties_are_normalised_and_sorted():
    fc = sectors.to_feature_collection([
        _feature("0451241E", "Collège André Malraux", SQUARE),
        _feature("045999L", "Secteur spécial", SQUARE, clg_insee=" ", clg_ville=None),
        _feature("0450003J", "Collège Jean Moulin", [[1.123456789, 47.0], *SQUARE[1:]]),
    ])
    ids = [f["properties"]["secteur_id"] for f in fc["features"]]
    assert ids == ["0450003J", "0451241E", "045999L"]
    malraux = fc["features"][1]["properties"]
    assert malraux == {"secteur_id": "0451241E", "secteur": "Collège André Malraux",
                       "commune": "Saint Jean De La Ruelle", "code_insee": "45285", "departement": "Loiret"}
    special = fc["features"][2]["properties"]
    assert special["code_insee"] is None and special["commune"] is None  # blanks are missing
    assert fc["features"][0]["geometry"]["coordinates"][0][0] == [1.1234568, 47.0]  # 7 decimals


def test_validation_and_comparison():
    fc = sectors.to_feature_collection([_feature("0451241E", "A", SQUARE), _feature("0450003J", "B", SQUARE)])
    assert sectors.validate(fc, {"0451241E", "0450003J"}) == []
    assert "missing" in sectors.validate(fc, {"0451241E", "0450999X"})[0]
    dup = {"features": fc["features"] + fc["features"][:1]}
    assert any("duplicate" in e for e in sectors.validate(dup, set()))

    changed = sectors.to_feature_collection(
        [_feature("0451241E", "A", BIGGER), _feature("0450004K", "C", SQUARE)]
    )
    diff = sectors.compare(changed, fc)
    assert diff["added"] == ["0450004K"] and diff["removed"] == ["0450003J"]
    assert diff["changed"] == ["0451241E (+20.0%)"]
    assert sectors.compare(fc, fc) == {"added": [], "removed": [], "changed": []}
