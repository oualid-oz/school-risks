// Admin page: users, roles, school assignments, email alerts, deactivation.
// The API enforces the rules (admins only, cannot demote or disable yourself or the last
// admin); this page shows its error messages as they come.

const ROLE_LABELS = { admin: "Administrateur", referent: "Référent", lecteur: "Lecteur" };
let editedUser = null; // null = creating

function setupAdmin() {
  document.getElementById("new-user-button").addEventListener("click", () => openUserDialog(null));
  const form = document.getElementById("user-form");
  form.elements.role.addEventListener("change", syncRoleFields);
  form.querySelector("[data-school-filter]").addEventListener("input", filterSchoolOptions);
  form.querySelector("[data-school-options]").addEventListener("change", updateSchoolCount);
  form.addEventListener("submit", saveUser);
}

async function showAdminPage() {
  showView("admin-page");
  document.getElementById("admin-error").hidden = true;
  const container = document.getElementById("user-table");
  container.replaceChildren(element("div", "empty-state", "Chargement des utilisateurs…"));
  try {
    renderUsers(await getJson("/auth/users"));
  } catch (error) {
    container.replaceChildren();
    showError("admin-error", `Impossible de charger les utilisateurs : ${error.message}`);
  }
}

function schoolName(uai) {
  return schools.find((s) => s.uai === uai)?.nom || uai;
}

function renderUsers(users) {
  const table = element("table");
  const head = element("thead");
  const headRow = element("tr");
  ["Identifiant", "Rôle", "Collèges", "E-mail", "Alertes", "Statut", ""].forEach((title) => {
    const th = element("th", "", title);
    th.scope = "col";
    headRow.append(th);
  });
  head.append(headRow);
  const body = element("tbody");
  users.forEach((user) => {
    const row = element("tr", user.active ? "" : "inactive");
    const schoolsCell = element("td");
    if (user.role === "referent") {
      schoolsCell.textContent = user.school_uais.length ? `${user.school_uais.length}` : "aucun";
      schoolsCell.title = user.school_uais.map(schoolName).join(", ");
    } else {
      schoolsCell.textContent = "tous";
    }
    const edit = element("button", "", "Modifier");
    edit.type = "button";
    edit.setAttribute("aria-label", `Modifier ${user.username}`);
    edit.addEventListener("click", () => openUserDialog(user));
    const actions = element("td");
    actions.append(edit);
    row.append(
      element("td", "strong", user.username + (user.id === currentUser.id ? " (vous)" : "")),
      element("td", "", ROLE_LABELS[user.role] || user.role),
      schoolsCell,
      element("td", "", user.email || "—"),
      element("td", "", user.alertes_email ? "oui" : "non"),
      element("td", "", user.active ? "Actif" : "Désactivé"),
      actions
    );
    body.append(row);
  });
  table.append(head, body);
  document.getElementById("user-table").replaceChildren(table);
}

function renderSchoolOptions(selected) {
  const container = document.querySelector("#user-form [data-school-options]");
  const sorted = [...schools].sort((a, b) => a.commune.localeCompare(b.commune, "fr") || a.nom.localeCompare(b.nom, "fr"));
  container.replaceChildren(...sorted.map((school) => {
    const label = element("label", "school-option");
    const box = element("input");
    box.type = "checkbox";
    box.value = school.uai;
    box.checked = selected.includes(school.uai);
    label.dataset.search = `${school.nom} ${school.commune} ${school.uai}`.toLowerCase();
    label.append(box, document.createTextNode(` ${school.nom} · ${school.commune} `), element("small", "muted", school.uai));
    return label;
  }));
  updateSchoolCount();
}

function filterSchoolOptions(event) {
  const query = event.target.value.trim().toLowerCase();
  document.querySelectorAll("#user-form .school-option").forEach((option) => {
    option.hidden = Boolean(query) && !option.dataset.search.includes(query);
  });
}

function selectedSchools() {
  return [...document.querySelectorAll("#user-form [data-school-options] input:checked")].map((box) => box.value);
}

function updateSchoolCount() {
  const count = selectedSchools().length;
  document.querySelector("#user-form [data-school-count]").textContent = `(${count} sélectionné${count > 1 ? "s" : ""})`;
}

function syncRoleFields() {
  const form = document.getElementById("user-form");
  const role = form.elements.role.value;
  form.querySelector("[data-referent-only]").hidden = role !== "referent";
  // Readers get no digest (the API refuses it).
  form.elements.alertes_email.disabled = role === "lecteur";
  if (role === "lecteur") form.elements.alertes_email.checked = false;
}

function openUserDialog(user) {
  editedUser = user;
  const form = document.getElementById("user-form");
  form.reset();
  formMessage(form, "");
  const creating = user === null;
  document.getElementById("user-title").textContent = creating ? "Nouvel utilisateur" : `Modifier ${user.username}`;
  form.elements.username.value = user?.username || "";
  form.elements.username.disabled = !creating; // usernames do not change
  form.elements.password.required = creating;
  form.querySelector("[data-password-label]").textContent = creating
    ? "Mot de passe (12 caractères minimum)"
    : "Nouveau mot de passe (laisser vide pour ne pas le changer)";
  form.elements.role.value = user?.role || "lecteur";
  form.elements.email.value = user?.email || "";
  form.elements.alertes_email.checked = Boolean(user?.alertes_email);
  form.elements.active.checked = user ? user.active : true;
  form.querySelector("[data-edit-only]").hidden = creating;
  form.querySelector("[data-school-filter]").value = "";
  renderSchoolOptions(user?.school_uais || []);
  syncRoleFields();
  document.getElementById("user-dialog").showModal();
}

async function saveUser(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const role = form.elements.role.value;
  const body = {
    role,
    school_uais: role === "referent" ? selectedSchools() : [],
    email: form.elements.email.value.trim() || null,
    alertes_email: form.elements.alertes_email.checked,
  };
  if (form.elements.password.value) body.password = form.elements.password.value;
  await submitting(form, async () => {
    if (editedUser) {
      body.active = form.elements.active.checked;
      await apiRequest(`/auth/users/${editedUser.id}`, { method: "PATCH", body });
    } else {
      await apiRequest("/auth/users", { method: "POST", body: { ...body, username: form.elements.username.value.trim() } });
    }
    document.getElementById("user-dialog").close();
    if (editedUser?.id === currentUser.id) currentUser = await getJson("/auth/me");
    await showAdminPage();
  });
}
