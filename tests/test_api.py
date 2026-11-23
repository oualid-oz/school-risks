from datetime import date, timedelta

API = "/api/v1"
UAI = "0450003J"  # Jean Moulin, Artenay


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "version": "dev"}


def test_frontend_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Se connecter" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_list_colleges_sorted_by_score(client):
    data = client.get(f"{API}/colleges").json()
    assert len(data) == 70
    scores = [c["score_global"] for c in data]
    assert scores == sorted(scores, reverse=True)


def test_filters(client):
    prives = client.get(f"{API}/colleges", params={"statut": "privé"}).json()
    assert len(prives) == 12 and all(c["statut"] == "privé" for c in prives)
    assert client.get(f"{API}/colleges", params={"q": "artenay"}).json()[0]["uai"] == UAI


def test_college_detail_and_profile(client):
    detail = client.get(f"{API}/colleges/{UAI}").json()
    assert detail["nom"] == "Jean Moulin"
    profile = detail["profil_risque"]
    assert set(profile["aleas"]) == {
        "inondation", "argiles", "mouvements_terrain", "industriel", "tmd", "nucleaire"
    }
    assert 0 <= profile["score_global"] <= 100
    assert client.get(f"{API}/colleges/{UAI}/risques").json() == profile


def test_history(client):
    hist = client.get(f"{API}/colleges/{UAI}/historique").json()
    assert [h["rentree"] for h in hist] == list(range(2016, 2026))


def test_unknown_college_404(client):
    assert client.get(f"{API}/colleges/XXXXXXXX").status_code == 404


def test_secteurs_geojson(client):
    gj = client.get(f"{API}/secteurs").json()
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 59
    assert "score_max" in gj["features"][0]["properties"]


def test_action_lifecycle(client):
    payload = {
        "uai": UAI,
        "titre": "Mettre à jour le PPMS risque TMD",
        "alea": "tmd",
        "priorite": "haute",
        "echeance": str(date.today() - timedelta(days=1)),
    }
    created = client.post(f"{API}/actions", json=payload)
    assert created.status_code == 201
    action = created.json()
    assert action["en_retard"] is True

    late = client.get(f"{API}/actions", params={"en_retard": True}).json()
    assert [a["id"] for a in late] == [action["id"]]

    done = client.patch(f"{API}/actions/{action['id']}", json={"statut": "termine"}).json()
    assert done["statut"] == "termine" and done["en_retard"] is False

    stats = client.get(f"{API}/stats").json()
    assert stats["actions_par_statut"] == {"termine": 1}

    assert client.delete(f"{API}/actions/{action['id']}").status_code == 204
    assert client.get(f"{API}/actions/{action['id']}").status_code == 404


def test_action_unknown_uai_rejected(client):
    r = client.post(f"{API}/actions", json={"uai": "0000000Z", "titre": "test action"})
    assert r.status_code == 422
