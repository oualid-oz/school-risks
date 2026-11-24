// Follow-up actions on the school page: list, create, edit, close, delete.
// Uses helpers from app.js (element, apiRequest, canEditSchool, frDate, showSchoolPage…).

const ACTION_HAZARDS = { ...HAZARD_LABELS, general: "Général" };
const ACTION_PRIORITIES = { basse: "Basse", normale: "Normale", haute: "Haute", critique: "Critique" };
const OPEN_STATUSES = ["a_faire", "en_cours"];

let actionDialogState = null; // { uai, action | null }

function fillSelect(select, options, value) {
  select.replaceChildren(...Object.entries(options).map(([key, text]) => {
    const option = element("option", "", text);
    option.value = key;
    return option;
  }));
  select.value = value;
}

function setupActionDialog() {
  const dialog = document.getElementById("action-dialog");
  const form = document.getElementById("action-form");
  const message = form.querySelector(".form-message");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const { uai, action } = actionDialogState;
    const data = new FormData(form);
    const value = (name) => String(data.get(name) || "").trim();
    const body = {
      titre: value("titre"),
      description: value("description") || null,
      alea: value("alea"),
      priorite: value("priorite"),
      statut: value("statut"),
      responsable: value("responsable") || null,
      echeance: value("echeance") || null,
    };
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    message.hidden = true;
    try {
      if (action) await apiRequest(`/actions/${action.id}`, { method: "PATCH", body });
      else await apiRequest("/actions", { method: "POST", body: { ...body, uai } });
      dialog.close();
      await afterActionChange(uai);
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
}

function openActionDialog(uai, action = null) {
  actionDialogState = { uai, action };
  const form = document.getElementById("action-form");
  form.reset();
  form.querySelector(".form-message").hidden = true;
  document.getElementById("action-title").textContent = action ? "Modifier l’action" : "Nouvelle action";
  fillSelect(form.elements.alea, ACTION_HAZARDS, action?.alea || "general");
  fillSelect(form.elements.priorite, ACTION_PRIORITIES, action?.priorite || "normale");
  fillSelect(form.elements.statut, STATUS_LABELS, action?.statut || "a_faire");
  form.elements.titre.value = action?.titre || "";
  form.elements.description.value = action?.description || "";
  form.elements.responsable.value = action?.responsable || "";
  form.elements.echeance.value = action?.echeance || "";
  document.getElementById("action-dialog").showModal();
  form.elements.titre.focus();
}

async function afterActionChange(uai) {
  statsStale = true; // open-actions count on the dashboard
  await showSchoolPage(uai, true);
}

async function actionRequest(button, uai, path, options) {
  button.disabled = true;
  try {
    await apiRequest(path, options);
    await afterActionChange(uai);
  } catch (error) {
    button.disabled = false;
    window.alert(error.message);
  }
}

function actionCard(action, uai, editable) {
  const closed = !OPEN_STATUSES.includes(action.statut);
  const card = element("article", `action-card${closed ? " closed" : ""}${action.en_retard ? " overdue" : ""}`);
  card.append(element("h3", "", action.titre));
  if (action.description) card.append(element("p", "", action.description));
  const tags = element("div", "action-tags");
  tags.append(
    element("span", "", ACTION_HAZARDS[action.alea] || action.alea),
    element("span", "", STATUS_LABELS[action.statut] || action.statut),
    element("span", "", PRIORITY_LABELS[action.priorite] || action.priorite)
  );
  if (action.echeance) {
    tags.append(element("span", action.en_retard ? "late" : "", `${action.en_retard ? "En retard · " : "Échéance · "}${frDate(action.echeance)}`));
  }
  if (action.responsable) tags.append(element("span", "", action.responsable));
  card.append(tags);

  if (editable) {
    const buttons = element("div", "card-buttons");
    const edit = element("button", "", "Modifier");
    edit.type = "button";
    edit.addEventListener("click", () => openActionDialog(uai, action));
    buttons.append(edit);
    if (!closed) {
      const done = element("button", "", "Terminer");
      done.type = "button";
      done.addEventListener("click", () => actionRequest(done, uai, `/actions/${action.id}`, { method: "PATCH", body: { statut: "termine" } }));
      buttons.append(done);
    }
    const remove = element("button", "danger", "Supprimer");
    remove.type = "button";
    remove.addEventListener("click", () => {
      if (window.confirm(`Supprimer l’action « ${action.titre} » ? La suppression reste tracée dans le journal d’audit.`)) {
        actionRequest(remove, uai, `/actions/${action.id}`, { method: "DELETE" });
      }
    });
    buttons.append(remove);
    card.append(buttons);
  }
  return card;
}

function canSeeActions(uai) {
  return currentUser?.role !== "referent" || currentUser.school_uais.includes(uai);
}

function renderActionsPanel(uai, actions) {
  if (actions === null) {
    const panel = element("section", "panel detail-panel actions-panel");
    panel.append(
      element("h2", "", "Actions de suivi"),
      element("div", "empty-state", "Les actions de ce collège sont visibles par ses référents et les administrateurs.")
    );
    return panel;
  }
  const editable = canEditSchool(uai);
  const open = actions.filter((a) => OPEN_STATUSES.includes(a.statut));
  const closed = actions.filter((a) => !OPEN_STATUSES.includes(a.statut));
  // Overdue first, then by due date (no date last).
  open.sort((a, b) => Number(b.en_retard) - Number(a.en_retard) || (a.echeance || "9999").localeCompare(b.echeance || "9999"));

  const panel = element("section", "panel detail-panel actions-panel");
  const head = element("div", "panel-head-row");
  const title = element("div");
  const late = open.filter((a) => a.en_retard).length;
  title.append(
    element("h2", "", "Actions de suivi"),
    element("p", "panel-caption", `${open.length} ouverte${open.length > 1 ? "s" : ""}${late ? ` dont ${late} en retard` : ""} · ${closed.length} terminée${closed.length > 1 ? "s" : ""} ou annulée${closed.length > 1 ? "s" : ""}.`)
  );
  head.append(title);
  if (editable) {
    const add = element("button", "primary-button", "Nouvelle action");
    add.type = "button";
    add.addEventListener("click", () => openActionDialog(uai));
    head.append(add);
  }
  panel.append(head);

  if (!actions.length) {
    panel.append(element("div", "empty-state", "Aucune action de suivi pour cet établissement."));
    return panel;
  }
  const list = element("div", "actions-list");
  open.forEach((a) => list.append(actionCard(a, uai, editable)));
  panel.append(list);
  if (closed.length) {
    const details = element("details", "closed-actions");
    details.append(element("summary", "", `Actions terminées ou annulées (${closed.length})`));
    const closedList = element("div", "actions-list");
    closed.forEach((a) => closedList.append(actionCard(a, uai, editable)));
    details.append(closedList);
    panel.append(details);
  }
  return panel;
}
