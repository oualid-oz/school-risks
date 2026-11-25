// What-if page: tune weights, level thresholds and clay class points, and compare the
// simulated levels and ranking with today's (POST /scores/simulation). Nothing is saved.

const SIM_HAZARDS = {
  inondation: "Inondation",
  argiles: "Argiles",
  mouvements_terrain: "Mouvements de terrain",
  industriel: "Industriel",
  tmd: "Transport de matières dangereuses",
  nucleaire: "Nucléaire",
  seisme: "Séisme (là où il existe)",
  radon: "Radon (là où il existe)",
  feu_foret: "Feu de forêt (là où il existe)",
  littoral: "Littoral (là où il existe)",
  avalanche: "Avalanche (là où il existe)",
};
const SIM_SEISME = { 1: "Zone 1 (très faible)", 2: "Zone 2 (faible)", 3: "Zone 3 (modérée)", 4: "Zone 4 (moyenne)", 5: "Zone 5 (forte)" };
const SIM_RADON = { 1: "Catégorie 1", 2: "Catégorie 2", 3: "Catégorie 3" };
const SIM_CLAY = { faible: "Exposition faible", moyen: "Exposition moyenne", fort: "Exposition forte", "non cartographié": "Non cartographié" };
const SIM_LEVELS = { faible: "Faible", moyen: "Modéré", eleve: "Élevé" };
// Class points per scale: [form group, API field, labels].
const SIM_SCALES = [["argiles", "argiles_points", SIM_CLAY], ["seisme", "seisme_points", SIM_SEISME], ["radon", "radon_points", SIM_RADON]];

let simulationDefaults = null;
let simulationTimer = null;
let simulationRequest = 0;

function numberField(name, labelText, value, { min, max, step }) {
  const label = element("label", "sim-field");
  const range = element("input");
  Object.assign(range, { type: "range", min, max, step, value, name });
  range.setAttribute("aria-label", labelText);
  const number = element("input");
  Object.assign(number, { type: "number", min, max, step, value });
  number.dataset.mirror = name;
  number.setAttribute("aria-label", `${labelText} (valeur)`);
  range.addEventListener("input", () => { number.value = range.value; });
  number.addEventListener("input", () => { range.value = number.value; });
  label.append(element("span", "", labelText), range, number);
  return label;
}

function setupSimulation() {
  const form = document.getElementById("simulation-form");
  form.addEventListener("input", scheduleSimulation);
  form.addEventListener("change", scheduleSimulation);
  document.getElementById("simulation-reset").addEventListener("click", () => {
    fillSimulationForm(simulationDefaults);
    runSimulation();
  });
}

function fillSimulationForm(params) {
  const form = document.getElementById("simulation-form");
  const group = (name) => form.querySelector(`[data-group="${name}"]`);
  const keepLegend = (fieldset) => fieldset.replaceChildren(fieldset.querySelector("legend"));
  ["poids", "seuils", ...SIM_SCALES.map(([name]) => name)].forEach((name) => keepLegend(group(name)));
  Object.entries(SIM_HAZARDS).forEach(([key, text]) =>
    group("poids").append(numberField(`poids.${key}`, text, params.poids[key], { min: 0, max: 5, step: 0.1 })));
  group("seuils").append(
    numberField("seuil_moyen", "Modéré à partir de", params.seuil_moyen, { min: 1, max: 99, step: 1 }),
    numberField("seuil_eleve", "Élevé à partir de", params.seuil_eleve, { min: 1, max: 100, step: 1 })
  );
  SIM_SCALES.forEach(([name, field, labels]) => Object.entries(labels).forEach(([key, text]) =>
    group(name).append(numberField(`${name}.${key}`, text, params[field]?.[key] ?? 0, { min: 0, max: 100, step: 5 }))));
}

function readSimulationForm() {
  const form = document.getElementById("simulation-form");
  const body = { poids: {}, tri: form.elements.tri.value };
  SIM_SCALES.forEach(([, field]) => { body[field] = {}; });
  form.querySelectorAll('input[type="range"]').forEach((input) => {
    const value = Number(input.value);
    const [prefix, key] = input.name.split(".");
    const scale = SIM_SCALES.find(([name]) => name === prefix);
    if (prefix === "poids") body.poids[key] = value;
    else if (scale) body[scale[1]][key] = value;
    else body[input.name] = value;
  });
  return body;
}

async function showSimulationPage() {
  showView("simulation-page");
  focusViewHeading("simulation-page");
  if (!simulationDefaults) {
    try {
      simulationDefaults = await getJson("/scores/parametres");
      fillSimulationForm(simulationDefaults);
    } catch (error) {
      showError("simulation-error", `Paramètres indisponibles : ${error.message}`);
      return;
    }
  }
  runSimulation();
}

function scheduleSimulation() {
  clearTimeout(simulationTimer);
  simulationTimer = setTimeout(runSimulation, 250);
}

async function runSimulation() {
  const request = ++simulationRequest;
  const error = document.getElementById("simulation-error");
  try {
    const result = await apiRequest("/scores/simulation", { method: "POST", body: readSimulationForm() });
    if (request !== simulationRequest) return; // a newer change is on its way
    error.hidden = true;
    renderSimulation(result);
  } catch (err) {
    if (request !== simulationRequest) return;
    showError("simulation-error", err.message);
  }
}

function levelBadge(level) {
  return element("span", `level-badge level-${level}`, SIM_LEVELS[level]);
}

function renderSimulation(result) {
  const summary = result.resume;
  const cards = [
    [String(summary.changements_de_niveau), "changent de niveau"],
    ...Object.entries(SIM_LEVELS).map(([key, text]) => [
      `${summary.par_niveau_actuel[key]} → ${summary.par_niveau_simule[key]}`, `${text.toLowerCase()}s`,
    ]),
    [`${summary.ecart_moyen > 0 ? "+" : ""}${String(summary.ecart_moyen).replace(".", ",")}`, "écart moyen du score"],
  ];
  document.getElementById("simulation-summary").replaceChildren(...cards.map(([value, text]) => {
    const card = element("div", "sim-card");
    card.append(element("strong", "", value), element("span", "", text));
    return card;
  }));

  const byPriority = result.tri === "priorite";
  const top = result.colleges.slice(0, 10);
  document.getElementById("simulation-top").replaceChildren(simulationTable(top, [
    ["Rang", (r) => `${r.rang_simule}${rankMove(r)}`],
    ["Collège", (r) => schoolCell(r)],
    [byPriority ? "Indice" : "Score", (r) => byPriority
      ? `${fmt(r.indice_actuel)} → ${fmt(r.indice_simule)}`
      : `${fmt(r.score_actuel)} → ${fmt(r.score_simule)}`],
    ["Niveau", (r) => levelChange(r)],
  ]));

  const changed = result.colleges.filter((r) => r.niveau_actuel !== r.niveau_simule)
    .sort((a, b) => Math.abs(b.ecart) - Math.abs(a.ecart));
  document.getElementById("simulation-changes-title").textContent = `Changements de niveau (${changed.length})`;
  document.getElementById("simulation-changes").replaceChildren(changed.length
    ? simulationTable(changed, [
      ["Collège", (r) => schoolCell(r)],
      ["Score", (r) => `${fmt(r.score_actuel)} → ${fmt(r.score_simule)}`],
      ["Niveau", (r) => levelChange(r)],
    ])
    : element("p", "empty-state", "Aucun collège ne change de niveau."));

  document.getElementById("simulation-env").textContent =
    Object.entries(result.variables).map(([key, value]) => `${key}=${value}`).join("\n");
}

function fmt(value) {
  return value == null ? "—" : String(value).replace(".", ",");
}

function rankMove(row) {
  const move = row.rang_actuel - row.rang_simule;
  return move > 0 ? ` ▲${move}` : move < 0 ? ` ▼${-move}` : "";
}

function levelChange(row) {
  const cell = element("span", "level-change");
  cell.append(levelBadge(row.niveau_actuel));
  if (row.niveau_simule !== row.niveau_actuel) cell.append(document.createTextNode(" → "), levelBadge(row.niveau_simule));
  return cell;
}

function schoolCell(row) {
  const button = element("button", "link-button", row.nom);
  button.type = "button";
  button.title = `${row.commune} · UAI ${row.uai}`;
  button.addEventListener("click", () => openSchool(row.uai));
  return button;
}

function simulationTable(rows, columns) {
  const table = element("table", "sim-table");
  const head = element("tr");
  columns.forEach(([title]) => {
    const th = element("th", "", title);
    th.scope = "col";
    head.append(th);
  });
  const thead = element("thead");
  thead.append(head);
  const body = element("tbody");
  rows.forEach((row) => {
    const tr = element("tr");
    columns.forEach(([, render]) => {
      const td = element("td");
      const value = render(row);
      if (value instanceof Node) td.append(value);
      else td.textContent = value;
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(thead, body);
  return table;
}
