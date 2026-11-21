// CivicLens - shared helpers + the floating "Your ticket" panel (used on every page)
window.CL = (() => {
  const KEYS = {
    draft: "civiclens.draft",          // {code, token} of the ticket waiting for confirmation
    mine: "civiclens.myTickets",       // {code: token} of tickets this browser filed
    minimized: "civiclens.panelMinimized",
    requests: "civiclens.myUrgencyRequests", // {requestId: token} of urgency requests this browser made
  };
  const MAX_PHOTOS = 4;
  const MAX_BYTES = 10 * 1024 * 1024;
  const ALLOWED = ["image/jpeg", "image/png", "image/webp"];

  // ---------------------------------------------------------------------
  // Storage (localStorage can throw in private mode, so always guard it)
  // ---------------------------------------------------------------------
  const store = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem(key);
        return v ? JSON.parse(v) : fallback;
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
    },
    remove(key) {
      try { localStorage.removeItem(key); } catch { /* ignore */ }
    },
  };

  const getDraft = () => store.get(KEYS.draft, null);
  const setDraft = (code, token) => store.set(KEYS.draft, { code, token });
  const clearDraft = () => store.remove(KEYS.draft);
  const myTickets = () => store.get(KEYS.mine, {});
  const addMyTicket = (code, token) => store.set(KEYS.mine, { ...myTickets(), [code]: token });
  const tokenFor = (code) => myTickets()[code] || null;
  const myRequests = () => store.get(KEYS.requests, {});
  const addMyRequest = (id, token) => store.set(KEYS.requests, { ...myRequests(), [id]: token });
  const requestTokenFor = (id) => myRequests()[id] || null;

  // ---------------------------------------------------------------------
  // DOM + API helpers
  // ---------------------------------------------------------------------

  // el("div", {class: "x", text: "hi", onclick: fn}, child1, child2)
  // Text is always set with textContent -> safe against HTML injection.
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  async function api(url, { method = "GET", body, token, headers: extra = {} } = {}) {
    const headers = { ...extra };
    if (token) headers["X-Edit-Token"] = token;
    let payload = body;
    if (body && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await fetch(url, { method, headers, body: payload });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Something went wrong (${res.status}). Please try again.`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // Check picked files. Returns {accepted: File[], error: string|null}
  function checkPhotos(files, alreadyCount, max = MAX_PHOTOS) {
    const accepted = [];
    let error = null;
    for (const f of files) {
      if (!ALLOWED.includes(f.type)) { error = "Only JPG, PNG or WEBP photos are allowed."; continue; }
      if (f.size > MAX_BYTES) { error = `"${f.name}" is larger than 10 MB.`; continue; }
      if (alreadyCount + accepted.length >= max) { error = `You can attach up to ${max} photos.`; break; }
      accepted.push(f);
    }
    return { accepted, error };
  }

  const slug = (s) => String(s || "").toLowerCase().replace(/\s+/g, "-");
  const severityBadge = (s) => el("span", { class: `badge ${slug(s)}`, text: s });
  const statusPill = (s) => el("span", { class: `status ${slug(s)}`, text: s });

  function formatDate(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
    });
  }

  function showError(node, message) {
    node.textContent = message || "";
    node.hidden = !message;
  }

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  // ---------------------------------------------------------------------
  // Floating ticket panel
  // ---------------------------------------------------------------------
  const Panel = (() => {
    const $ = (id) => document.getElementById(id);
    const panel = $("ticket-panel");
    if (!panel) return { init() {} };

    const sections = ["tp-view", "tp-edit-form", "tp-loading", "tp-done"].map($);
    let ticket = null;
    let token = null;
    let keepPhotos = [];   // existing photo file names kept while editing
    let newPhotos = [];    // File objects added while editing

    function only(id) {
      sections.forEach((s) => (s.hidden = s.id !== id));
      showError($("tp-error"), "");
    }

    function setMinimized(min) {
      panel.classList.toggle("minimized", min);
      $("tp-body").hidden = min;
      $("tp-bar").setAttribute("aria-expanded", String(!min));
      $("tp-toggle-icon").textContent = min ? "+" : "–";
      $("tp-toggle-label").textContent = min ? "Expand" : "Minimise";
      store.set(KEYS.minimized, min);
    }

    function renderView(t) {
      $("tp-bar-code").textContent = t.ticket_id;
      const sev = $("tp-severity");
      sev.textContent = t.severity;
      sev.className = `badge ${slug(t.severity)}`;
      $("tp-category").textContent = t.category;
      $("tp-summary").textContent = t.summary;
      $("tp-department").textContent = t.department;
      $("tp-language").textContent = t.detected_language || "—";
      $("tp-location").textContent = t.location || "Not mentioned";
      $("tp-severity-reason").textContent = t.severity_reason || "—";

      $("tp-translation-wrap").hidden = !t.english_translation;
      $("tp-translation").textContent = t.english_translation || "";

      $("tp-photos-wrap").hidden = !t.photo_urls.length;
      $("tp-photos").replaceChildren(
        ...t.photo_urls.map((url) =>
          el("a", { href: url, target: "_blank", rel: "noopener", class: "thumb" },
            el("img", { src: url, alt: "Complaint photo" })))
      );
      $("tp-image-obs").textContent = t.image_observations || "";
    }

    function show(t, { maximize = false } = {}) {
      ticket = t;
      panel.hidden = false;
      $("tp-bar-state").textContent = "· awaiting confirmation";
      panel.classList.remove("confirmed");
      renderView(t);
      only("tp-view");
      setMinimized(maximize ? false : store.get(KEYS.minimized, false));
    }

    // ----- edit mode -----
    function renderEditPhotos() {
      const items = [
        ...keepPhotos.map((name) =>
          el("div", { class: "thumb" },
            el("img", { src: `/uploads/${name}`, alt: "Attached photo" }),
            el("button", {
              type: "button", class: "thumb-remove", "aria-label": "Remove photo", text: "×",
              onclick: () => { keepPhotos = keepPhotos.filter((n) => n !== name); renderEditPhotos(); },
            }))),
        ...newPhotos.map((file) =>
          el("div", { class: "thumb new" },
            el("img", { src: URL.createObjectURL(file), alt: "New photo" }),
            el("button", {
              type: "button", class: "thumb-remove", "aria-label": "Remove photo", text: "×",
              onclick: () => { newPhotos = newPhotos.filter((f) => f !== file); renderEditPhotos(); },
            }))),
      ];
      $("tp-edit-photos").replaceChildren(...items);
      $("tp-add-photos").closest("label").hidden = keepPhotos.length + newPhotos.length >= MAX_PHOTOS;
    }

    function startEdit() {
      $("tp-text").value = ticket.complaint_text || "";
      $("tp-location-input").value = ticket.location_input || "";
      keepPhotos = [...ticket.photos];
      newPhotos = [];
      renderEditPhotos();
      showError($("tp-edit-error"), "");
      only("tp-edit-form");
      $("tp-text").focus();
    }

    async function submitEdit(e) {
      e.preventDefault();
      const text = $("tp-text").value.trim();
      if (!text && keepPhotos.length + newPhotos.length === 0) {
        showError($("tp-edit-error"), "Please describe the issue or keep at least one photo.");
        return;
      }
      const form = new FormData();
      form.append("complaint", text);
      form.append("location", $("tp-location-input").value.trim());
      keepPhotos.forEach((name) => form.append("keep_photos", name));
      newPhotos.forEach((file) => form.append("photos", file));

      $("tp-loading-text").innerHTML = "<strong>AI is re-reading your complaint…</strong>";
      only("tp-loading");
      try {
        const data = await api(`/api/tickets/${ticket.ticket_id}/edit`, { method: "POST", body: form, token });
        ticket = data.ticket;
        renderView(ticket);
        only("tp-view");
        emit("civiclens:ticket-updated", { ticket });
      } catch (err) {
        only("tp-edit-form");
        showError($("tp-edit-error"), err.message);
      }
    }

    // ----- confirm -----
    async function confirm() {
      $("tp-loading-text").innerHTML = "<strong>Sending to the department…</strong>";
      only("tp-loading");
      try {
        const data = await api(`/api/tickets/${ticket.ticket_id}/confirm`, { method: "POST", token });
        ticket = data.ticket;
        clearDraft();
        addMyTicket(ticket.ticket_id, token);
        panel.classList.add("confirmed");
        $("tp-bar-state").textContent = "· confirmed";
        $("tp-done-dept").textContent = ticket.department;
        $("tp-view-link").href = `/departments?dept=${encodeURIComponent(ticket.category)}#${ticket.ticket_id}`;
        only("tp-done");
        setMinimized(false);
        emit("civiclens:ticket-confirmed", { ticket });
      } catch (err) {
        only("tp-view");
        showError($("tp-error"), err.message);
      }
    }

    // ----- wiring -----
    $("tp-bar").addEventListener("click", () => setMinimized(!panel.classList.contains("minimized")));
    $("tp-edit").addEventListener("click", startEdit);
    $("tp-cancel").addEventListener("click", () => only("tp-view"));
    $("tp-edit-form").addEventListener("submit", submitEdit);
    $("tp-confirm").addEventListener("click", confirm);
    $("tp-close").addEventListener("click", () => (panel.hidden = true));
    $("tp-add-photos").addEventListener("change", (e) => {
      const { accepted, error } = checkPhotos([...e.target.files], keepPhotos.length + newPhotos.length);
      newPhotos.push(...accepted);
      e.target.value = "";
      showError($("tp-edit-error"), error);
      renderEditPhotos();
    });

    async function init() {
      const draft = getDraft();
      if (!draft) return;
      try {
        const data = await api(`/api/tickets/${draft.code}`, { token: draft.token });
        token = draft.token;
        if (data.ticket.status === "Draft") {
          show(data.ticket);
          emit("civiclens:panel-ready", { ticket: data.ticket });
        } else {
          // confirmed in another tab - remember it as ours and stop showing the panel
          clearDraft();
          addMyTicket(draft.code, draft.token);
        }
      } catch (err) {
        if (err.status === 404 || err.status === 403) clearDraft();
      }
    }

    return { init };
  })();

  Panel.init();

  return {
    el, api, checkPhotos, severityBadge, statusPill, formatDate, showError,
    getDraft, setDraft, tokenFor, myTickets, addMyRequest, requestTokenFor,
    MAX_PHOTOS,
    expandPanelNextTime: () => store.set(KEYS.minimized, false),
  };
})();
