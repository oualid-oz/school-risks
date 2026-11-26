// Data quality page (#qualite, administrators) and data error reports (school page, everyone).
// GET /qualite, GET/PATCH /signalements, POST /colleges/{uai}/signalements.

const REPORT_FIELDS = {
  position: "Position du collège",
  identite: "Nom, statut ou commune",
  effectif: "Effectif",
  secteur: "Secteur de recrutement",
  risque: "Une donnée de risque",
  autre: "Autre",
};
const REPORT_STATUS = { ouvert: "Ouvert", traite: "Traité", rejete: "Rejeté" };
const SECTOR_ORIGINS = {
  departement: "carte du département",
  carte_scolaire: "carte scolaire",
  carte_scolaire_approchee: "carte scolaire (approchée)",
  mixte: "mixte",
};
let reportSchool = null; // UAI of the school being reported

function setupQuality() {
  const form = document.getElementById("report-form");
  form.elements.champ.replaceChildren(...Object.entries(REPORT_FIELDS).map(([value, text]) => {
    const option = element("option", "", text);
    option.value = value;
    return option;
  }));
  form.addEventListener("submit", sendReport);
}

// ---- report a data error (school page) ----------------------------------------------
function reportButton(uai) {
  const button = element("button", "export-button", "Signaler une erreur de données");
  button.type = "button";
  button.title = "Une position, un effectif, un secteur ou un risque vous semble faux";
  button.addEventListener("click", () => {
    reportSchool = uai;
    const form = document.getElementById("report-form");
    form.reset();
    formMessage(form, "");
    form.querySelector('button[type="submit"]').hidden = false;
    document.getElementById("report-dialog").showModal();
  });
  return button;
}

async function sendReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  await submitting(form, async () => {
    await apiRequest(`/colleges/${encodeURIComponent(reportSchool)}/signalements`, {
      method: "POST",
      body: { champ: form.elements.champ.value, message: form.elements.message.value.trim() },
    });
    form.reset();
    form.querySelector('button[type="submit"]').hidden = true;
    formMessage(form, "Merci : votre signalement a été transmis aux administrateurs de ce territoire.");
  });
}

// ---- quality page ---------------------------------------------------------------------
async function showQualityPage() {
  showView("quality-page");
  focusViewHeading("quality-page");
  document.getElementById("quality-error").hidden = true;
  const targets = ["quality-table", "quality-reports", "quality-schools"].map((id) => document.getElementById(id));
  targets.forEach((t) => t.replaceChildren(element("div", "empty-state", "Chargement…")));
  try {
    const [quality, reports] = await Promise.all([
      getJson(`/qualite${depParam()}`),
      getJson(`/signalements?statut=ouvert${depParam("&")}`),
    ]);
    renderQualityTable(quality.departements);
    renderReports(reports);
    renderIncomplete(quality.colleges_incomplets);
  } catch (error) {
    targets.forEach((t) => t.replaceChildren());
    showError("quality-error", `Impossible de charger la qualité des données : ${error.message}`);
  }
}

function coverageCell(item) {
  const cell = element("td", "num");
  if (item.taux == null) {
    cell.textContent = "—";
    return cell;
  }
  cell.textContent = `${item.taux} %`;
  cell.className = `num coverage ${item.taux >= 95 ? "good" : item.taux >= 50 ? "partial" : "poor"}`;
  cell.title = `${item.renseignes} / ${item.total}`;
  return cell;
}

function renderQualityTable(departements) {
  const checks = departements.length ? Object.entries(departements[0].couverture) : [];
  const table = element("table", "quality-table");
  const caption = element("caption", "", "Part des collèges pour lesquels chaque source fournit la donnée");
  const head = element("thead");
  const row = element("tr");
  ["Département", "Collèges", ...checks.map(([, c]) => c.libelle), "Origine des secteurs", "Signalements ouverts"]
    .forEach((title) => {
      const th = element("th", "", title);
      th.scope = "col";
      row.append(th);
    });
  head.append(row);
  const body = element("tbody");
  departements.forEach((d) => {
    const tr = element("tr");
    const name = element("th", "", `${d.nom || "Département"} (${d.code})`);
    name.scope = "row";
    const origins = Object.entries(d.secteurs.par_source)
      .map(([source, n]) => `${n} ${SECTOR_ORIGINS[source] || source}`).join(", ") || "aucune source";
    tr.append(name, element("td", "num", nf.format(d.colleges)),
      ...checks.map(([key]) => coverageCell(d.couverture[key])),
      element("td", "", origins), element("td", "num", nf.format(d.signalements_ouverts)));
    body.append(tr);
  });
  table.append(caption, head, body);
  const wrap = element("div", "table-scroll");
  wrap.append(table);
  document.getElementById("quality-table").replaceChildren(wrap);
}

function renderReports(reports) {
  const container = document.getElementById("quality-reports");
  if (!reports.length) {
    container.replaceChildren(element("p", "empty-state", "Aucun signalement ouvert."));
    return;
  }
  container.replaceChildren(...reports.map((report) => {
    const card = element("article", "report-card");
    const link = element("a", "", report.nom || report.uai);
    link.href = `#school/${encodeURIComponent(report.uai)}`;
    const title = element("h3");
    title.append(link, document.createTextNode(` · ${REPORT_FIELDS[report.champ] || report.champ}`));
    const when = new Date(report.created_at).toLocaleDateString("fr-FR");
    card.append(title, element("p", "", report.message),
      element("small", "muted", `Signalé par ${report.created_by_username} le ${when}`));
    if (currentUser.role === "admin") {
      const form = element("form", "report-close");
      const label = element("label", "ppms-field");
      const answer = element("textarea");
      answer.name = "reponse";
      answer.maxLength = 2000;
      answer.rows = 2;
      label.append(element("span", "", "Réponse (visible par la personne qui a signalé)"), answer);
      const buttons = element("div", "alerts-buttons");
      ["rejete", "traite"].forEach((statut) => {
        const button = element("button", statut === "traite" ? "primary" : "", statut === "traite" ? "Marquer traité" : "Rejeter");
        button.type = "submit";
        button.value = statut;
        buttons.append(button);
      });
      form.append(label, buttons, element("p", "form-message", ""));
      form.querySelector(".form-message").setAttribute("role", "alert");
      form.querySelector(".form-message").hidden = true;
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const statut = event.submitter?.value || "traite";
        await submitting(form, async () => {
          await apiRequest(`/signalements/${report.id}`, {
            method: "PATCH", body: { statut, reponse: answer.value.trim() || null },
          });
          card.remove();
          if (!container.children.length) renderReports([]);
        });
      });
      card.append(form);
    }
    return card;
  }));
}

function renderIncomplete(schoolsWithGaps) {
  const container = document.getElementById("quality-schools");
  if (!schoolsWithGaps.length) {
    container.replaceChildren(element("p", "empty-state", "Toutes les données sont renseignées."));
    return;
  }
  const shown = schoolsWithGaps.slice(0, 50);
  const list = element("ul", "gap-list");
  shown.forEach((school) => {
    const item = element("li");
    const link = element("a", "", school.nom);
    link.href = `#school/${encodeURIComponent(school.uai)}`;
    item.append(link, element("small", "muted", ` · ${school.commune} (${school.departement}) : ${school.manquants.join(", ")}`));
    list.append(item);
  });
  const note = schoolsWithGaps.length > shown.length
    ? element("p", "muted", `${nf.format(shown.length)} premiers sur ${nf.format(schoolsWithGaps.length)} collèges incomplets (les plus incomplets d’abord).`)
    : element("p", "muted", `${nf.format(schoolsWithGaps.length)} collège(s) incomplet(s).`);
  container.replaceChildren(note, list);
}
