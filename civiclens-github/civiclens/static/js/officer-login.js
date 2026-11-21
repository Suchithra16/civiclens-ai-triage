// CivicLens - officer login page
(() => {
  const { api, showError } = CL;
  const $ = (id) => document.getElementById(id);

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("off-name").value.trim();
    const department = $("off-dept").value;
    const password = $("off-password").value;

    if (name.length < 2) return showError($("login-error"), "Please enter your name.");
    if (!department) return showError($("login-error"), "Please choose your department.");
    if (!password) return showError($("login-error"), "Please enter your password.");

    $("login-btn").disabled = true;
    try {
      await api("/api/officer/login", { method: "POST", body: { name, department, password } });
      window.location.href = "/officer";
    } catch (err) {
      showError($("login-error"), err.message);
      $("login-btn").disabled = false;
    }
  });
})();
