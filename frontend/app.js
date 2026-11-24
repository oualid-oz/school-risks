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
let rankingMode = "score"; // "score": risk of the location, "priorite": pupils exposed × risk
let schoolMarkers = [];
let currentUser = null;
let statsStale = false; // a PPMS change happened: refresh the dashboard numbers on return
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

async function apiRequest(path, { method = "GET", body } = {}) {
  const headers = accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    if (response.status === 401 && accessToken) {
      accessToken = null;
      showLogin("Votre session a expiré. Veuillez vous reconnecter.");
    }
    let message = `Erreur ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
      // FastAPI validation errors: [{loc, msg}, …]
      else if (Array.isArray(payload.detail)) {
        message = payload.detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join(" · ");
      }
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function getJson(path) {
  return apiRequest(path);
}

// Exports need the Authorization header, so a plain <a href> cannot be used: fetch the
// file, then hand it to the browser as a download.
async function downloadFile(path, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Préparation…";
  try {
    const response = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${accessToken}` } });
    if (response.status === 401) {
      accessToken = null;
      showLogin("Votre session a expiré. Veuillez vous reconnecter.");
      return;
    }
    if (!response.ok) {
      let message = `Erreur ${response.status}`;
      try { message = (await response.json()).detail || message; } catch { /* not JSON */ }
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] || "export";
    const url = URL.createObjectURL(await response.blob());
    const link = element("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    window.alert(`Export impossible : ${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function exportButton(labelText, path, title) {
  const button = element("button", "export-button", labelText);
  button.type = "button";
  if (title) button.title = title;
  button.addEventListener("click", () => downloadFile(typeof path === "function" ? path() : path, button));
  return button;
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

const VIEWS = ["login-screen", "dashboard", "school-page", "admin-page"];

function showView(id) {
  VIEWS.forEach((view) => { document.getElementById(view).hidden = view !== id; });
}

// Phones: the account items live in a dropdown (desktop shows them inline, CSS only).
function setupAccountMenu() {
  const toggle = document.getElementById("account-menu-toggle");
  const menu = document.getElementById("account-menu");
  const setOpen = (open) => {
    menu.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
  };
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = !menu.classList.contains("open");
    setOpen(open);
    if (open) menu.querySelector("a:not([hidden]), button")?.focus();
  });
  // Close after choosing an item, on a click elsewhere, or with Escape.
  menu.addEventListener("click", (event) => {
    if (event.target.closest("a, button")) setOpen(false);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#account-controls")) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.classList.contains("open")) {
      setOpen(false);
      toggle.focus();
    }
  });
}

// After a view change, put keyboard and screen-reader users on the new page's title
// (otherwise the focus stays on the button that was clicked, now hidden).
function focusViewHeading(id) {
  const heading = document.querySelector(`#${id} h1`);
  if (!heading) return;
  heading.setAttribute("tabindex", "-1");
  heading.focus({ preventScroll: false });
}

function showLogin(message = "") {
  showView("login-screen");
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  document.getElementById("account-controls").hidden = true;
  const error = document.getElementById("login-error");
  error.textContent = message;
  error.hidden = !message;
}

function showAccount(user) {
  currentUser = user;
  document.getElementById("admin-link").hidden = user.role !== "admin";
  document.getElementById("login-screen").hidden = true;
  document.getElementById("account-controls").hidden = false;
  document.getElementById("account-name").textContent = `${user.username} · ${user.role}`;
  document.querySelector(".menu-initial").textContent = user.username.charAt(0).toUpperCase();
}

function renderStats(stats) {
  const openActions = (stats.actions_par_statut?.a_faire || 0) + (stats.actions_par_statut?.en_cours || 0);
  const values = [
    [
      `${nf.format(stats.ppms_par_statut?.a_jour || 0)} / ${nf.format(stats.nb_colleges)}`,
      "PPMS à jour",
      `${nf.format(stats.ppms_par_statut?.ancien || 0)} à mettre à jour · ${nf.format(stats.ppms_par_statut?.absent || 0)} non renseignés`,
    ],
    [nf.format(stats.nb_eleves), "Élèves scolarisés", `dont ${nf.format(stats.eleves_par_niveau?.eleve || 0)} en risque élevé`],
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
  const byPriority = rankingMode === "priorite";
  const index = school.indice_priorite;
  button.setAttribute(
    "aria-label",
    byPriority
      ? `Ouvrir ${school.nom}, indice de priorité ${index ?? "inconnu"}, score ${school.score_global}`
      : `Ouvrir ${school.nom}, score ${school.score_global}`
  );
  const number = element("span", "rank", String(rank).padStart(2, "0"));
  const name = element("span", "school-name");
  const pupils = school.effectif_total == null ? "effectif inconnu" : `${nf.format(school.effectif_total)} élèves`;
  const detail = byPriority
    ? `${school.commune} · ${pupils} · score ${school.score_global}`
    : `${school.commune} · ${school.alea_principal.replaceAll("_", " ")}`;
  name.append(element("strong", "", school.nom), element("small", "", detail));
  const score = element("span", `score ${riskClass(school.score_global)}`);
  if (byPriority) {
    score.append(document.createTextNode(index == null ? "—" : nf.format(Math.round(index))));
    score.append(element("small", "", "indice"));
  } else {
    score.append(document.createTextNode(String(school.score_global)));
    score.append(element("small", "", " / 100"));
  }
  button.append(number, name, score);
  button.addEventListener("click", () => openSchool(school.uai));
  return button;
}

// Same order as the API (`GET /colleges?tri=`): unknown indices last.
function rankSchools(list) {
  const sorted = [...list];
  if (rankingMode === "priorite") {
    sorted.sort((a, b) => (b.indice_priorite ?? -1) - (a.indice_priorite ?? -1) || b.score_global - a.score_global);
  } else {
    sorted.sort((a, b) => b.score_global - a.score_global);
  }
  return sorted;
}

const RANKING_CAPTIONS = {
  score: "Classés selon leur score global de risque.",
  priorite: "Classés selon les élèves exposés, pondérés par le risque (score × effectif / 100).",
};

function setRankingMode(mode) {
  rankingMode = mode;
  document.querySelectorAll("[data-ranking]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.ranking === mode));
  });
  document.getElementById("ranking-caption").textContent = RANKING_CAPTIONS[mode];
  document.getElementById("legend-size").hidden = mode !== "priorite";
  renderSchoolList(schools);
  styleMarkers();
}

function renderSchoolList(list) {
  const container = document.getElementById("school-list");
  container.replaceChildren(...rankSchools(list).slice(0, 8).map((school, index) => schoolRow(school, index + 1)));
  document.getElementById("school-count").textContent = String(list.length);
  document.getElementById("map-count").textContent = `${list.length} collèges`;
}

function popupContent(school) {
  const popup = element("div", "map-popup");
  popup.append(
    element("strong", "", school.nom),
    element("small", "", `${school.commune} · ${school.score_global}/100`),
    element("small", "", school.indice_priorite == null
      ? "Indice de priorité : effectif inconnu"
      : `Indice de priorité : ${nf.format(Math.round(school.indice_priorite))} (${nf.format(school.effectif_total)} élèves)`)
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
    // A click anywhere (polygons included) asks PostGIS for the sector and nearby schools
    // (spatial.js); hovering a sector just names it.
    onEachFeature: (feature, layer) => {
      const props = feature.properties;
      const score = props.score_max == null ? "aucun collège rattaché" : `score maximal ${props.score_max}/100`;
      layer.bindTooltip(`${props.secteur || `Secteur ${props.secteur_id}`} · ${score}`, { sticky: true });
    },
  }).addTo(map);
  enableMapPointSelection(); // spatial.js

  const markerGroup = L.featureGroup();
  schoolMarkers = [];
  schools.forEach((school) => {
    if (!Number.isFinite(school.lat) || !Number.isFinite(school.lon)) return;
    const marker = L.circleMarker([school.lat, school.lon], {
      radius: 6,
      color: "#fff",
      weight: 2,
      fillColor: riskColor(school.score_global),
      fillOpacity: 1,
      bubblingMouseEvents: false, // a click on a school opens its popup, not a point search
    });
    marker.bindPopup(popupContent(school));
    marker.bindTooltip(element("span", "", school.nom), { direction: "top", offset: [0, -6] });
    marker.addTo(markerGroup);
    schoolMarkers.push({ marker, school });
  });
  markerGroup.addTo(map);
  styleMarkers();
  const bounds = sectors.getBounds();
  mapBounds = bounds.isValid() ? bounds : null;
  mapNeedsFit = true;
  fitMapToLoiret();
}

// In priority mode the dot's area is proportional to the index (radius ∝ √index), so a
// big school with a medium score stands out; the colour always stays the risk level.
function markerRadius(school) {
  if (rankingMode !== "priorite") return 6;
  if (school.indice_priorite == null) return 4;
  return Math.max(4, Math.min(22, 0.85 * Math.sqrt(school.indice_priorite)));
}

function styleMarkers() {
  const ordered = [...schoolMarkers].sort((a, b) => markerRadius(b.school) - markerRadius(a.school));
  ordered.forEach(({ marker, school }) => {
    marker.setRadius(markerRadius(school));
    marker.bringToFront(); // big dots first, small ones on top, so none is hidden
  });
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

function renderSchool(school, actions, history, ppms) {
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
  const uaiParam = encodeURIComponent(school.uai);
  const exportsBar = element("div", "export-bar");
  exportsBar.append(
    exportButton("Fiche PDF", `/colleges/${uaiParam}/fiche.pdf`, "Fiche risques à imprimer ou à joindre"),
    exportButton("Actions (CSV)", `/exports/actions.csv?uai=${uaiParam}`, "Actions de suivi, pour Excel")
  );
  heading.append(exportsBar);
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

  const actionsPanel = renderActionsPanel(school.uai, actions); // actions.js

  root.replaceChildren(header);
  const grid = element("div", "detail-grid");
  grid.append(riskPanel, historyPanel, ppmsPanel(school.uai, ppms), actionsPanel);
  root.append(grid);
  currentCharts.forEach((chart) => chart.destroy());
  currentCharts = [];
  renderRiskChart(radar, profile);
  renderHistoryChart(historyCanvas, history);
}

// ---- PPMS ---------------------------------------------------------------------------
const PPMS_STATUS = {
  a_jour: ["À jour", "ppms-ok"],
  ancien: ["À mettre à jour", "ppms-late"],
  absent: ["Non renseigné", "ppms-missing"],
};
const DRILL_LABELS = { risques_majeurs: "Risques majeurs", intrusion: "Intrusion", incendie: "Incendie" };

function frDate(isoDate) {
  return isoDate ? new Date(`${isoDate}T00:00:00`).toLocaleDateString("fr-FR") : "—";
}

function todayIso() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function canEditSchool(uai) {
  if (!currentUser) return false;
  return currentUser.role === "admin" || (currentUser.role === "referent" && currentUser.school_uais.includes(uai));
}

async function refreshDashboardNumbers() {
  try {
    const [stats, collegeData] = await Promise.all([getJson("/stats"), getJson("/colleges")]);
    schools = collegeData;
    renderStats(stats);
    statsStale = false;
  } catch (error) {
    showError("dashboard-error", `Impossible de mettre à jour les chiffres : ${error.message}`);
  }
}

function field(labelText, input) {
  const label = element("label", "ppms-field");
  label.append(element("span", "", labelText), input);
  return label;
}

function ppmsForm(title, fields, onSubmit) {
  const form = element("form", "ppms-form");
  const error = element("p", "ppms-error");
  error.hidden = true;
  const submit = element("button", "", "Enregistrer");
  submit.type = "submit";
  form.append(element("h3", "", title), ...fields, submit, error);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      await onSubmit();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      submit.disabled = false;
    }
  });
  return form;
}

function dateInput() {
  const input = element("input");
  input.type = "date";
  input.required = true;
  input.max = todayIso();
  input.value = todayIso();
  return input;
}

function commentInput() {
  const input = element("input");
  input.type = "text";
  input.maxLength = 1000;
  input.placeholder = "Facultatif";
  return input;
}

async function afterPpmsChange(uai) {
  statsStale = true;
  await showSchoolPage(uai, true);
}

function historyList(title, entries, describe, onDelete) {
  const section = element("div", "ppms-history-list");
  section.append(element("h3", "", title));
  if (!entries.length) {
    section.append(element("p", "muted small", "Aucune entrée."));
    return section;
  }
  const list = element("ul");
  entries.forEach((entry) => {
    const item = element("li");
    const textPart = element("div");
    textPart.append(element("strong", "", describe(entry)), element("small", "", `${entry.commentaire ? `${entry.commentaire} · ` : ""}saisi par ${entry.created_by_username}`));
    item.append(textPart);
    if (onDelete) {
      const remove = element("button", "ppms-delete", "Supprimer");
      remove.type = "button";
      remove.setAttribute("aria-label", `Supprimer : ${describe(entry)}`);
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Supprimer « ${describe(entry)} » ? La suppression reste tracée dans le journal d’audit.`)) return;
        remove.disabled = true;
        try {
          await onDelete(entry);
        } catch (err) {
          remove.disabled = false;
          window.alert(err.message);
        }
      });
      item.append(remove);
    }
    list.append(item);
  });
  section.append(list);
  return section;
}

function ppmsPanel(uai, ppms) {
  const status = ppms.statut;
  const [label, badgeClass] = PPMS_STATUS[status.statut];
  const panel = element("section", "panel detail-panel ppms-panel");
  panel.append(
    element("h2", "", "PPMS — plan particulier de mise en sûreté"),
    element("p", "panel-caption", `Année scolaire ${status.annee_scolaire}`)
  );

  const summary = element("div", "ppms-summary");
  const dates = status.derniere_mise_a_jour
    ? `Dernière mise à jour : ${frDate(status.derniere_mise_a_jour)} · à revoir avant le ${frDate(status.prochaine_mise_a_jour)}`
    : "Aucune mise à jour enregistrée.";
  summary.append(element("span", `ppms-badge ${badgeClass}`, label), element("span", "ppms-dates", dates));
  panel.append(summary);
  if (status.raisons.length) {
    const reasons = element("ul", "alert-list");
    status.raisons.forEach((reason) => reasons.append(element("li", "", reason)));
    panel.append(reasons);
  }
  const drills = element("div", "action-tags ppms-drills");
  Object.entries(DRILL_LABELS).forEach(([kind, kindLabel]) => {
    const day = status.exercices_annee[kind];
    drills.append(element("span", day ? "" : "late", `${kindLabel} : ${day ? frDate(day) : "aucun cette année"}`));
  });
  panel.append(drills);

  const editable = canEditSchool(uai);
  const base = `/colleges/${encodeURIComponent(uai)}/ppms`;
  if (editable) {
    const revisionDate = dateInput();
    const revisionComment = commentInput();
    const drillType = element("select");
    Object.entries(DRILL_LABELS).forEach(([kind, kindLabel]) => {
      const option = element("option", "", kindLabel);
      option.value = kind;
      drillType.append(option);
    });
    const drillDate = dateInput();
    const drillComment = commentInput();
    const forms = element("div", "ppms-forms");
    forms.append(
      ppmsForm("Mise à jour du PPMS", [field("Date de mise à jour", revisionDate), field("Commentaire", revisionComment)], async () => {
        await apiRequest(`${base}/revisions`, {
          method: "POST",
          body: { date_mise_a_jour: revisionDate.value, commentaire: revisionComment.value || null },
        });
        await afterPpmsChange(uai);
      }),
      ppmsForm("Exercice réalisé", [field("Type", drillType), field("Date", drillDate), field("Commentaire", drillComment)], async () => {
        await apiRequest(`${base}/exercices`, {
          method: "POST",
          body: { type_exercice: drillType.value, date_exercice: drillDate.value, commentaire: drillComment.value || null },
        });
        await afterPpmsChange(uai);
      })
    );
    panel.append(forms);
  }

  const historyGrid = element("div", "ppms-history");
  historyGrid.append(
    historyList("Mises à jour", ppms.revisions, (r) => `Mise à jour du ${frDate(r.date_mise_a_jour)}`, editable
      ? async (r) => { await apiRequest(`${base}/revisions/${r.id}`, { method: "DELETE" }); await afterPpmsChange(uai); }
      : null),
    historyList("Exercices", ppms.exercices, (e) => `${DRILL_LABELS[e.type_exercice]} · ${frDate(e.date_exercice)}`, editable
      ? async (e) => { await apiRequest(`${base}/exercices/${e.id}`, { method: "DELETE" }); await afterPpmsChange(uai); }
      : null)
  );
  panel.append(historyGrid);
  return panel;
}

async function openSchool(uai) {
  location.hash = `school/${encodeURIComponent(uai)}`;
}

async function showSchoolPage(uai, keepFocus = false) {
  showView("school-page");
  document.getElementById("school-error").hidden = true;
  document.getElementById("school-content").replaceChildren(element("div", "empty-state", "Chargement de la fiche…"));
  try {
    const [school, history, actions, ppms] = await Promise.all([
      getJson(`/colleges/${encodeURIComponent(uai)}`),
      getJson(`/colleges/${encodeURIComponent(uai)}/historique`),
      // Referents only see the actions of their own schools (the API answers 403 otherwise),
      // but every school's risk page stays readable.
      canSeeActions(uai) ? getJson(`/actions?uai=${encodeURIComponent(uai)}`) : Promise.resolve(null),
      getJson(`/colleges/${encodeURIComponent(uai)}/ppms`),
    ]);
    renderSchool(school, actions, history, ppms);
    if (!keepFocus) focusViewHeading("school-page");
  } catch (error) {
    document.getElementById("school-content").replaceChildren();
    showError("school-error", `Impossible de charger cette fiche : ${error.message}`);
  }
}

function route() {
  if (!currentUser) return;
  const match = location.hash.match(/^#school\/([^/]+)$/);
  if (match) showSchoolPage(decodeURIComponent(match[1]));
  else if (location.hash === "#admin" && currentUser.role === "admin") showAdminPage();
  else {
    currentCharts.forEach((chart) => chart.destroy());
    currentCharts = [];
    showView("dashboard");
    if (statsStale) refreshDashboardNumbers();
    if (map) setTimeout(() => { map.invalidateSize(); fitMapToLoiret(); }, 0);
  }
}

async function init() {
  document.getElementById("back-button").addEventListener("click", () => { location.hash = "dashboard"; });
  setupAccountDialog(); // account.js
  setupActionDialog(); // actions.js
  setupAdmin(); // admin.js
  setupSpatial(); // spatial.js
  setupAccountMenu();
  // Skip link: move the focus without touching the URL hash (the hash is the router).
  document.querySelector(".skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    const target = document.querySelector(".view:not([hidden]) h1") || document.getElementById("contenu");
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus();
  });
  // Any [data-close] button closes its dialog.
  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close]");
    if (closer) closer.closest("dialog")?.close();
  });
  document.getElementById("export-list").replaceWith(
    Object.assign(
      exportButton("Exporter (CSV)", () => `/exports/colleges.csv?tri=${rankingMode}`, "Tous les collèges, dans l’ordre affiché, pour Excel"),
      { id: "export-list" }
    )
  );
  document.querySelectorAll("[data-ranking]").forEach((button) => {
    button.addEventListener("click", () => setRankingMode(button.dataset.ranking));
  });
  setupSearch();
  window.addEventListener("hashchange", route);
  document.getElementById("logout-button").addEventListener("click", () => {
    accessToken = null;
    currentUser = null;
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

// After every deferred script (actions.js, account.js, admin.js) has run.
document.addEventListener("DOMContentLoaded", init);
