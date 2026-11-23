// "Mon compte": email alerts (admins and referents) and own password.
// Uses helpers from app.js (apiRequest, currentUser, accessToken…).

function formMessage(form, text, isError) {
  const message = form.querySelector(".form-message");
  message.textContent = text;
  message.classList.toggle("error", Boolean(isError));
  message.hidden = !text;
}

async function submitting(form, work) {
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  formMessage(form, "");
  try {
    await work();
  } catch (error) {
    formMessage(form, error.message, true);
  } finally {
    submit.disabled = false;
  }
}

function setupAccountDialog() {
  const dialog = document.getElementById("account-dialog");
  const alertsForm = document.getElementById("alerts-form");
  const passwordForm = document.getElementById("password-form");
  const email = document.getElementById("alerts-email");
  const enabled = document.getElementById("alerts-enabled");

  document.getElementById("account-button").addEventListener("click", () => {
    // Digests are for admins and referents (the API refuses them for readers).
    alertsForm.hidden = !["admin", "referent"].includes(currentUser?.role);
    email.value = currentUser?.email || "";
    enabled.checked = Boolean(currentUser?.alertes_email);
    passwordForm.reset();
    formMessage(alertsForm, "");
    formMessage(passwordForm, "");
    dialog.showModal();
  });

  alertsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitting(alertsForm, async () => {
      currentUser = await apiRequest("/auth/me", {
        method: "PATCH",
        body: { email: email.value.trim() || null, alertes_email: enabled.checked },
      });
      formMessage(alertsForm, enabled.checked ? "Alertes activées." : "Alertes désactivées.");
    });
  });

  passwordForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const { current, new: next, confirm } = passwordForm.elements;
    if (next.value !== confirm.value) {
      formMessage(passwordForm, "Les deux nouveaux mots de passe ne sont pas identiques.", true);
      return;
    }
    submitting(passwordForm, async () => {
      const token = await apiRequest("/auth/me/password", {
        method: "POST",
        body: { current_password: current.value, new_password: next.value },
      });
      // Older tokens are revoked by the change: keep the session with the new one.
      accessToken = token.access_token;
      passwordForm.reset();
      formMessage(passwordForm, "Mot de passe modifié. Vos autres sessions sont déconnectées.");
    });
  });
}
