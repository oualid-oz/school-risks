const API = "/api/v1";
const HAZARD_LABELS = {
  inondation: "Inondation",
  argiles: "Argiles",
  mouvements_terrain: "Mouvements de terrain",
  industriel: "Industriel",
  tmd: "Transport dangereux",
  nucleaire: "Nucléaire",
};
const STATUS_LABELS = { a_faire: "À faire", en_cours: "En cours", termine: "Terminée", annule: "Annulée" };
const PRIORITY_LABELS = { basse: "Priorité basse", normale: "Priorité normale", haute: "Priorité haute", critique: "Priorité critique" };
const nf = new Intl.NumberFormat("fr-FR");

let map;
let mapBounds = null;
let mapNeedsFit = false;
let schools = [];
let currentCharts = [];
let accessToken = null;

function riskClass(score) {
  if (score == null) return "risk-unknown";
  if (score >= 50) return "risk-high";
  if (score >= 30) return "risk-medium";
  return "risk-low";
}

function riskColor(score) {
  if (score == null) return "#aeb8b0";
  if (score >= 50) return "#d65b50";
  if (score >= 30) return "#e5a749";
  return "#54a879";
}

function text(value, fallback = "Non renseigné") {
  return value == null || value === "" ? fallback : String(value);
}

async function getJson(path) {
  const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
  const response = await fetch(`${API}${path}`, { headers });
  if (!response.ok) {
    if (response.status === 401 && accessToken) {
      accessToken = null;
      showLogin("Votre session a expiré. Veuillez vous reconnecter.");
    }
    let message = `Erreur ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) message = body.detail;
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
    }
    throw new Error(message);
  }
  return response.json();
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function showError(id, message) {
  const banner = document.getElementById(id);
  banner.textContent = message;
  banner.hidden = false;
}

function showLogin(message = "") {
  document.getElementById("login-screen").hidden = false;
  document.getElementById("dashboard").hidden = true;
  document.getElementById("school-page").hidden = true;
  document.getElementById("account-controls").hidden = true;
  const error = document.getElementById("login-error");
  error.textContent = message;
  error.hidden = !message;
}

function showAccount(user) {
  document.getElementById("login-screen").hidden = true;
  document.getElementById("account-controls").hidden = false;
  document.getElementById("account-name").textContent = `${user.username} · ${user.role}`;
}

function renderStats(stats) {
  const openActions = (stats.actions_par_statut?.a_faire || 0) + (stats.actions_par_statut?.en_cours || 0);
  const values = [
    [nf.format(stats.nb_colleges), "Collèges suivis", "dans le département"],
    [nf.format(stats.nb_eleves), "Élèves scolarisés", "effectif de référence"],
    [nf.format(stats.par_niveau?.eleve || 0), "Risque élevé", "score global ≥ 50"],
    [nf.format(openActions), "Actions ouvertes", "à faire ou en cours"],
  ];
  const grid = document.getElementById("stats-grid");
  grid.replaceChildren(...values.map(([value, label, note], index) => {
    const card = element("article", "stat-card");
    card.append(element("span", "stat-label", label), element("strong", index === 2 ? "risk-high" : ""), element("span", "stat-note", note));
    card.children[1].textContent = value;
    return card;
  }));
}

function schoolRow(school, rank) {
  const button = element("button", "school-row");
  button.type = "button";
  button.setAttribute("aria-label", `Ouvrir ${school.nom}, score ${school.score_global}`);
  const number = element("span", "rank", String(rank).padStart(2, "0"));
  const name = element("span", "school-name");
  name.append(element("strong", "", school.nom), element("small", "", `${school.commune} · ${school.alea_principal.replaceAll("_", " ")}`));
  const score = element("span", `score ${riskClass(school.score_global)}`);
  score.append(document.createTextNode(String(school.score_global)));
  score.append(element("small", "", " / 100"));
  button.append(number, name, score);
  button.addEventListener("click", () => openSchool(school.uai));
  return button;
}

function renderSchoolList(list) {
  const container = document.getElementById("school-list");
  container.replaceChildren(...list.slice(0, 8).map((school, index) => schoolRow(school, index + 1)));
  document.getElementById("school-count").textContent = String(list.length);
  document.getElementById("map-count").textContent = `${list.length} collèges`;
}

function popupContent(school) {
  const popup = element("div", "map-popup");
  popup.append(
    element("strong", "", school.nom),
    element("small", "", `${school.commune} · ${school.score_global}/100`)
  );
  const button = element("button", "", "Voir la fiche");
  button.type = "button";
  button.addEventListener("click", () => openSchool(school.uai));
  popup.append(button);
  return popup;
}

function renderMap(geojson) {
  if (!window.L) throw new Error("La bibliothèque cartographique n’a pas pu être chargée.");
  if (map) map.remove(); // a second login re-renders the dashboard
  map = L.map("map", { scrollWheelZoom: false, zoomControl: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(map);

  const sectors = L.geoJSON(geojson, {
    style: (feature) => {
      const score = feature.properties.score_max;
      return { color: riskColor(score), weight: 1.2, opacity: 0.9, fillColor: riskColor(score), fillOpacity: score == null ? 0.12 : 0.25 };
    },
    onEachFeature: (feature, layer) => {
      const props = feature.properties;
      const popup = element("div", "map-popup");
      popup.append(
        element("strong", "", props.nom || `Secteur ${props.secteur_id}`),
        element("small", "", props.score_max == null ? "Aucun collège rattaché" : `Score maximal : ${props.score_max}/100`)
      );
      layer.bindPopup(popup);
    },
  }).addTo(map);

  const markerGroup = L.featureGroup();
  schools.forEach((school) => {
    if (!Number.isFinite(school.lat) || !Number.isFinite(school.lon)) return;
    const marker = L.circleMarker([school.lat, school.lon], {
      radius: 6,
      color: "#fff",
      weight: 2,
      fillColor: riskColor(school.score_global),
      fillOpacity: 1,
    });
    marker.bindPopup(popupContent(school));
    marker.bindTooltip(element("span", "", school.nom), { direction: "top", offset: [0, -6] });
    marker.addTo(markerGroup);
  });
  markerGroup.addTo(map);
  const bounds = sectors.getBounds();
  mapBounds = bounds.isValid() ? bounds : null;
  mapNeedsFit = true;
  fitMapToLoiret();
}

// Leaflet can only fit bounds once the map container is visible and has a size.
// The dashboard is still hidden behind the login screen when the map is built, so the
// first fit is retried when the dashboard is shown (see route()).
function fitMapToLoiret() {
  if (!map || !mapNeedsFit) return;
  const container = map.getContainer();
  if (!container.offsetWidth || !container.offsetHeight) return;
  map.invalidateSize();
  if (mapBounds) map.fitBounds(mapBounds, { padding: [14, 14] });
  else map.setView([47.9, 2.3], 8);
  mapNeedsFit = false;
}

function setupSearch() {
  const input = document.getElementById("school-search");
  const results = document.getElementById("search-results");
  input.addEventListener("input", () => {
    const query = input.value.trim().toLocaleLowerCase("fr");
    if (!query) {
      results.hidden = true;
      results.replaceChildren();
      return;
    }
    const matches = schools.filter((school) => `${school.nom} ${school.commune} ${school.uai}`.toLocaleLowerCase("fr").includes(query)).slice(0, 7);
    results.replaceChildren(...matches.map((school) => {
      const button = element("button", "search-result");
      button.type = "button";
      button.append(element("strong", "", school.nom), element("small", "", `${school.commune} · ${school.score_global}/100`));
      button.addEventListener("click", () => {
        results.hidden = true;
        input.value = "";
        openSchool(school.uai);
      });
      return button;
    }));
    results.hidden = matches.length === 0;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".search-wrap")) results.hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      input.focus();
    }
  });
}

function addChart(canvas, config) {
  if (!window.Chart) throw new Error("La bibliothèque des graphiques n’a pas pu être chargée.");
  const chart = new Chart(canvas, config);
  currentCharts.push(chart);
}

function renderRiskChart(canvas, profile) {
  addChart(canvas, {
    type: "radar",
    data: {
      labels: Object.keys(profile.aleas).map((key) => HAZARD_LABELS[key] || key),
      datasets: [{
        data: Object.values(profile.aleas),
        backgroundColor: "rgba(35, 130, 88, .16)",
        borderColor: "#238258",
        pointBackgroundColor: "#238258",
        pointBorderColor: "#fff",
        pointRadius: 3,
        borderWidth: 2,
      }],
    },
    options: {
      maintainAspectRatio: false,
      scales: { r: { min: 0, max: 100, ticks: { stepSize: 25, backdropColor: "transparent", color: "#89938c", font: { size: 9 } }, pointLabels: { color: "#59665e", font: { size: 10 } }, grid: { color: "#e9eeea" }, angleLines: { color: "#e9eeea" } } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (context) => ` ${context.formattedValue}/100` } } },
    },
  });
}

function renderHistoryChart(canvas, history) {
  const labels = history.map((record) => record.annee_scolaire || String(record.rentree));
  addChart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "IPS",
          data: history.map((record) => record.ips),
          borderColor: "#3479a5",
          backgroundColor: "#3479a5",
          yAxisID: "y",
          tension: 0.25,
          spanGaps: true,
        },
        {
          label: "Réussite DNB (%)",
          data: history.map((record) => record.dnb_taux_reussite),
          borderColor: "#238258",
          backgroundColor: "#238258",
          yAxisID: "y1",
          tension: 0.25,
          spanGaps: true,
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7, color: "#89938c", font: { size: 9 } }, grid: { display: false } },
        y: { position: "left", title: { display: true, text: "IPS", color: "#3479a5", font: { size: 10 } }, ticks: { color: "#89938c", font: { size: 9 } }, grid: { color: "#edf0ed" } },
        y1: { position: "right", title: { display: true, text: "Réussite DNB (%)", color: "#238258", font: { size: 10 } }, ticks: { color: "#89938c", font: { size: 9 } }, grid: { drawOnChartArea: false } },
      },
      plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 6, color: "#59665e", font: { size: 10 }, padding: 15 } } },
    },
  });
}

function actionCard(action) {
  const card = element("article", "action-card");
  card.append(element("h3", "", action.titre));
  if (action.description) card.append(element("p", "", action.description));
  const tags = element("div", "action-tags");
  tags.append(
    element("span", "", HAZARD_LABELS[action.alea] || action.alea),
    element("span", "", STATUS_LABELS[action.statut] || action.statut),
    element("span", "", PRIORITY_LABELS[action.priorite] || action.priorite)
  );
  if (action.echeance) tags.append(element("span", action.en_retard ? "late" : "", `${action.en_retard ? "En retard · " : "Échéance · "}${action.echeance}`));
  if (action.responsable) tags.append(element("span", "", action.responsable));
  card.append(tags);
  return card;
}

function renderSchool(school, actions, history) {
  const root = document.getElementById("school-content");
  const profile = school.profil_risque;
  const header = element("div", "school-header");
  const heading = element("div");
  heading.append(
    element("p", "eyebrow", "FICHE ÉTABLISSEMENT"),
    element("h1", "", school.nom),
    element("p", "school-location", `${school.commune} · ${school.statut}`)
  );
  const metadata = element("div", "school-meta");
  metadata.append(
    element("span", "meta-chip", `UAI ${school.uai}`),
    element("span", "meta-chip", `${nf.format(school.effectif_total || 0)} élèves`),
    element("span", "meta-chip", `IPS ${text(school.ips)}`),
    element("span", "meta-chip", `Secteur ${text(school.secteur_id)}`)
  );
  heading.append(metadata);
  const score = element("div", `school-score-card ${riskClass(profile.score_global)}`);
  score.append(
    element("small", "", "Score global"),
    element("strong", "", String(profile.score_global)),
    element("span", "", profile.niveau === "eleve" ? "Élevé" : profile.niveau === "moyen" ? "Modéré" : "Faible")
  );
  header.append(heading, score);

  const riskPanel = element("section", "panel detail-panel");
  riskPanel.append(element("h2", "", "Profil des risques"), element("p", "panel-caption", `Principal : ${HAZARD_LABELS[profile.alea_principal] || profile.alea_principal}`));
  const radarWrap = element("div", "chart-wrap");
  const radar = element("canvas");
  radar.setAttribute("aria-label", "Graphique radar des scores par aléa");
  radarWrap.append(radar);
  riskPanel.append(radarWrap);
  if (profile.alertes?.length) {
    const list = element("ul", "alert-list");
    profile.alertes.forEach((alert) => list.append(element("li", "", alert)));
    riskPanel.append(list);
  }

  const historyPanel = element("section", "panel detail-panel");
  historyPanel.append(element("h2", "", "Évolution de l’établissement"), element("p", "panel-caption", "IPS et taux de réussite au DNB par année scolaire."));
  const historyWrap = element("div", "chart-wrap history-chart");
  const historyCanvas = element("canvas");
  historyCanvas.setAttribute("aria-label", "Graphique de l’évolution de l’IPS et des résultats au DNB");
  historyWrap.append(historyCanvas);
  historyPanel.append(historyWrap);

  const actionsPanel = element("section", "panel detail-panel actions-panel");
  actionsPanel.append(element("h2", "", "Actions de suivi"), element("p", "panel-caption", `${actions.length} action${actions.length > 1 ? "s" : ""} enregistrée${actions.length > 1 ? "s" : ""}.`));
  const actionList = element("div", actions.length ? "actions-list" : "empty-state");
  if (actions.length) actions.forEach((action) => actionList.append(actionCard(action)));
  else actionList.textContent = "Aucune action de suivi pour cet établissement.";
  actionsPanel.append(actionList);

  root.replaceChildren(header);
  const grid = element("div", "detail-grid");
  grid.append(riskPanel, historyPanel, actionsPanel);
  root.append(grid);
  currentCharts.forEach((chart) => chart.destroy());
  currentCharts = [];
  renderRiskChart(radar, profile);
  renderHistoryChart(historyCanvas, history);
}

async function openSchool(uai) {
  location.hash = `school/${encodeURIComponent(uai)}`;
}

async function showSchoolPage(uai) {
  document.getElementById("dashboard").hidden = true;
  document.getElementById("school-page").hidden = false;
  document.getElementById("school-error").hidden = true;
  document.getElementById("school-content").replaceChildren(element("div", "empty-state", "Chargement de la fiche…"));
  try {
    const [school, history, actions] = await Promise.all([
      getJson(`/colleges/${encodeURIComponent(uai)}`),
      getJson(`/colleges/${encodeURIComponent(uai)}/historique`),
      getJson(`/actions?uai=${encodeURIComponent(uai)}`),
    ]);
    renderSchool(school, actions, history);
  } catch (error) {
    document.getElementById("school-content").replaceChildren();
    showError("school-error", `Impossible de charger cette fiche : ${error.message}`);
  }
}

function route() {
  const match = location.hash.match(/^#school\/([^/]+)$/);
  const dashboard = document.getElementById("dashboard");
  const schoolPage = document.getElementById("school-page");
  if (match) showSchoolPage(decodeURIComponent(match[1]));
  else {
    currentCharts.forEach((chart) => chart.destroy());
    currentCharts = [];
    schoolPage.hidden = true;
    dashboard.hidden = false;
    if (map) setTimeout(() => { map.invalidateSize(); fitMapToLoiret(); }, 0);
  }
}

async function init() {
  document.getElementById("back-button").addEventListener("click", () => { location.hash = "dashboard"; });
  setupSearch();
  window.addEventListener("hashchange", route);
  document.getElementById("logout-button").addEventListener("click", () => {
    accessToken = null;
    location.hash = "dashboard";
    showLogin();
  });
  document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const error = document.getElementById("login-error");
    error.hidden = true;
    submit.disabled = true;
    try {
      const response = await fetch(`${API}/auth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(new FormData(form)),
      });
      if (!response.ok) throw new Error("Identifiant ou mot de passe invalide.");
      const token = await response.json();
      accessToken = token.access_token;
      const user = await getJson("/auth/me");
      form.reset();
      showAccount(user);
      await loadDashboard();
      route();
    } catch (loginError) {
      accessToken = null;
      error.textContent = loginError.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  showLogin();
}

async function loadDashboard() {
  try {
    const [stats, sectorData, collegeData] = await Promise.all([
      getJson("/stats"),
      getJson("/secteurs?with_scores=true"),
      getJson("/colleges"),
    ]);
    schools = collegeData;
    renderStats(stats);
    renderSchoolList(schools);
    renderMap(sectorData);
    if (location.hash) route();
  } catch (error) {
    showError("dashboard-error", `Impossible de charger le tableau de bord : ${error.message}`);
    document.getElementById("map-count").textContent = "Données indisponibles";
  }
}

init();
