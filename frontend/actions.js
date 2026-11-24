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
      responsable_user_id: value("responsable_user_id") ? Number(value("responsable_user_id")) : null,
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
  const assignee = form.elements.responsable_user_id;
  fillSelect(assignee, { "": "— Personne —" }, "");
  document.getElementById("action-dialog").showModal();
  form.elements.titre.focus();
  loadAssignables(uai, assignee, action?.responsable_user_id);
}

// Admins and the school's referents (GET /actions/assignables).
async function loadAssignables(uai, select, current) {
  try {
    const people = await getJson(`/actions/assignables?uai=${encodeURIComponent(uai)}`);
    const options = { "": "— Personne —" };
    people.forEach((p) => { options[String(p.id)] = `${p.username} (${p.role === "admin" ? "administrateur" : "référent"})`; });
    fillSelect(select, options, current ? String(current) : "");
  } catch (error) {
    select.title = `Liste indisponible : ${error.message}`;
  }
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
  if (action.responsable_username) tags.append(element("span", "assignee", `Assignée à ${action.responsable_username}`));
  if (action.responsable) tags.append(element("span", "", action.responsable));
  card.append(tags);
  const comment = action.dernier_commentaire;
  if (comment) {
    const quote = element("blockquote", "last-comment");
    quote.append(
      element("p", "", comment.texte),
      element("small", "", `${comment.author_username}, ${frDateTime(comment.created_at)}`)
    );
    card.append(quote);
  }

  const buttons = element("div", "card-buttons");
  const historyButton = element("button", "", "Historique");
  historyButton.type = "button";
  historyButton.setAttribute("aria-label", `Historique et commentaires de « ${action.titre} »`);
  historyButton.addEventListener("click", () => openHistory(action, uai, editable));
  buttons.append(historyButton);
  card.append(buttons);
  if (editable) {
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
  }
  return card;
}

// ---- history and comments --------------------------------------------------------------
const FIELD_LABELS = {
  titre: "Titre", description: "Description", alea: "Aléa", priorite: "Priorité", statut: "Statut",
  responsable: "Responsable", echeance: "Échéance", assigne: "Assignée à",
};
let historyState = null; // { action, uai, editable, changed }

function frDateTime(iso) {
  return new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

function fieldValue(field, value) {
  if (value == null || value === "") return "—";
  if (field === "alea") return ACTION_HAZARDS[value] || value;
  if (field === "priorite") return ACTION_PRIORITIES[value] || value;
  if (field === "statut") return STATUS_LABELS[value] || value;
  if (field === "echeance") return frDate(value);
  return value;
}

function historyItem(event) {
  const item = element("li", `history-item history-${event.kind}`);
  const head = element("div", "history-head");
  const what = { creation: "a créé l’action", modification: "a modifié", commentaire: "a commenté" }[event.kind] || event.kind;
  head.append(element("strong", "", event.author_username), document.createTextNode(` ${what} · `), element("time", "", frDateTime(event.created_at)));
  item.append(head);
  if (event.kind === "modification" && event.changes) {
    // Backfilled from the audit log: only the field names were kept.
    const unknown = event.changes.every((c) => c.avant == null && c.apres == null);
    if (unknown) {
      item.append(element("p", "muted small", `${event.changes.map((c) => FIELD_LABELS[c.champ] || c.champ).join(", ")} (anciennes valeurs non conservées)`));
    } else {
      const list = element("ul", "history-changes");
      event.changes.forEach((c) => {
        list.append(element("li", "", `${FIELD_LABELS[c.champ] || c.champ} : ${fieldValue(c.champ, c.avant)} → ${fieldValue(c.champ, c.apres)}`));
      });
      item.append(list);
    }
  }
  if (event.kind === "commentaire") {
    item.append(element("p", "history-comment", event.texte));
    if (event.deletable) {
      const remove = element("button", "ppms-delete", "Supprimer");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Supprimer ce commentaire ? La suppression reste tracée dans le journal d’audit.")) return;
        remove.disabled = true;
        try {
          await apiRequest(`/actions/${historyState.action.id}/commentaires/${event.id}`, { method: "DELETE" });
          historyState.changed = true;
          await loadHistory();
        } catch (error) {
          remove.disabled = false;
          window.alert(error.message);
        }
      });
      item.append(remove);
    }
  }
  return item;
}

async function loadHistory() {
  const list = document.getElementById("history-list");
  try {
    const events = await getJson(`/actions/${historyState.action.id}/historique`);
    list.replaceChildren(...(events.length ? events.map(historyItem) : [element("li", "muted small", "Aucun événement enregistré.")]));
  } catch (error) {
    list.replaceChildren(element("li", "ppms-error", `Historique indisponible : ${error.message}`));
  }
}

function openHistory(action, uai, editable) {
  historyState = { action, uai, editable, changed: false };
  const dialog = document.getElementById("history-dialog");
  const form = document.getElementById("comment-form");
  document.getElementById("history-title").textContent = action.titre;
  form.hidden = !editable; // readers read, admins and the school's referents comment
  form.reset();
  form.querySelector(".form-message").hidden = true;
  document.getElementById("history-list").replaceChildren(element("li", "muted small", "Chargement…"));
  dialog.showModal();
  loadHistory();
}

function setupHistoryDialog() {
  const dialog = document.getElementById("history-dialog");
  const form = document.getElementById("comment-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = form.querySelector(".form-message");
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    message.hidden = true;
    try {
      await apiRequest(`/actions/${historyState.action.id}/commentaires`, {
        method: "POST",
        body: { texte: form.elements.texte.value },
      });
      form.reset();
      historyState.changed = true;
      await loadHistory();
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  // The card shows the last comment: refresh the page when the dialog closes after a change.
  dialog.addEventListener("close", () => {
    if (historyState?.changed) afterActionChange(historyState.uai);
  });
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
