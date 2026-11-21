// CivicLens - officer dashboard (own department only)
(() => {
  const { el, api, severityBadge, statusPill, formatDate, showError } = CL;
  const $ = (id) => document.getElementById(id);
  const DAY = 24 * 3600 * 1000;
  const POLL_MS = 60 * 1000; // check for new notifications every minute

  let tickets = [];
  let filter = "open";
  let notifications = [];
  const openDetails = new Set(); // ticket ids whose citizen details are expanded
  const drafts = new Map();      // unsaved officer edits: id -> {inProgress, resolved, action}

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  function handleError(err) {
    if (err.status === 401) {
      window.location.href = "/officer/login";
      return true;
    }
    toast(err.message, true);
    return false;
  }

  let toastTimer = null;
  function toast(message, isError = false) {
    const t = $("toast");
    t.textContent = message;
    t.classList.toggle("error-toast", isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 3500);
  }

  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // Deadline chip: "Due in 3 days", "Due today", "Overdue by 2 days", "Resolved"
  function dueChip(t) {
    if (t.status === "Resolved") {
      return el("span", { class: "due done", text: `✓ Resolved ${formatDate(t.action_updated_at)}` });
    }
    if (!t.deadline) return null;
    const deadline = new Date(t.deadline);
    const diff = deadline - new Date();
    const date = deadline.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    if (diff < 0) {
      const days = Math.floor(-diff / DAY);
      return el("span", { class: "due overdue", text: days ? `⏰ Overdue by ${plural(days, "day")}` : "⏰ Overdue today",
        title: `Deadline was ${date}` });
    }
    const days = Math.floor(diff / DAY);
    return el("span", { class: "due" + (days < 1 ? " soon" : ""),
      text: days < 1 ? `Due today (${date})` : `Due in ${plural(days, "day")} (${date})` });
  }

  // ---------------------------------------------------------------------
  // Load + header
  // ---------------------------------------------------------------------
  async function loadMe() {
    try {
      const { officer } = await api("/api/officer/me");
      $("officer-chip").textContent = `👤 ${officer.name} · ${officer.department_icon} ${officer.department_name}`;
    } catch (err) {
      handleError(err);
    }
  }

  async function loadTickets() {
    try {
      const data = await api("/api/officer/tickets");
      tickets = data.tickets;
      renderIntro(data);
      renderList();
    } catch (err) {
      handleError(err);
    }
  }

  function renderIntro(data) {
    const d = data.department;
    $("off-dept-icon").textContent = d.icon;
    $("off-dept-name").textContent = d.name;
    const s = data.sla_days;
    $("off-sla").textContent =
      `Deadlines from confirmation: Critical ${s.Critical} days · High ${s.High} · Medium ${s.Medium} · Low ${s.Low}. ` +
      "Status and action taken are shown to citizens as soon as you submit them.";
    const c = data.counts;
    const stat = (label, value, cls) =>
      el("div", { class: `stat ${cls}` }, el("div", { class: "stat-value", text: value }), el("div", { class: "stat-label", text: label }));
    $("off-stats").replaceChildren(
      stat("Open", c.open, "total"),
      stat("Overdue", c.overdue, "overdue"),
      stat("Critical open", c.critical, "critical"),
      stat("Resolved", c.resolved, "resolved"),
    );
  }

  // ---------------------------------------------------------------------
  // Complaint list
  // ---------------------------------------------------------------------
  function renderList() {
    const shown = tickets.filter((t) =>
      filter === "all" ? true : filter === "open" ? t.status !== "Resolved" : t.status === filter);
    $("off-list").replaceChildren(...shown.map(card));
    $("off-empty").hidden = shown.length > 0;
    $("off-empty").textContent = filter === "open" ? "No open complaints. 🎉" : "Nothing here yet.";
  }

  function card(t) {
    const overdue = t.status !== "Resolved" && t.deadline && new Date(t.deadline) < new Date();
    const urgentAccepted = t.urgency_requests.some((u) => u.status === "Accepted");
    const expanded = openDetails.has(t.ticket_id);

    return el("article", {
      class: "ticket-card off-card" + (overdue ? " is-overdue" : "") + ` sev-${t.severity.toLowerCase()}`,
      id: `o-${t.ticket_id}`,
    },
      el("header", { class: "tc-head" },
        el("span", { class: "mono tc-id", text: t.ticket_id }),
        severityBadge(t.severity),
        statusPill(t.status),
        urgentAccepted ? el("span", { class: "tag-urgent", text: "⚡ Urgent" }) : null,
        el("span", { class: "spacer" }),
        dueChip(t)),

      el("h3", { class: "tc-summary", text: t.summary }),

      el("div", { class: "tc-meta" },
        t.location ? el("span", { text: `📍 ${t.location}` }) : null,
        el("span", { text: `🗓 Filed ${formatDate(t.confirmed_at)}` }),
        t.detected_language && t.detected_language !== "None" ? el("span", { text: `🌐 ${t.detected_language}` }) : null),

      t.severity_reason ? el("p", { class: "tc-reason", text: `Why ${t.severity}: ${t.severity_reason}` }) : null,

      el("button", {
        type: "button", class: "btn-link-plain teal",
        text: expanded ? "Hide citizen's complaint" : "Show citizen's complaint & photos",
        onclick: () => { expanded ? openDetails.delete(t.ticket_id) : openDetails.add(t.ticket_id); refresh(t); },
      }),
      expanded ? detailsBox(t) : null,

      urgencyBox(t),
      t.feedback.length ? feedbackBox(t) : null,
      updateBox(t),
    );
  }

  function refresh(t) {
    const old = $(`o-${t.ticket_id}`);
    if (old) old.replaceWith(card(t));
  }

  function detailsBox(t) {
    return el("div", { class: "off-details" },
      el("div", { class: "box-label", text: "Citizen's complaint (original)" }),
      el("p", { class: "original", text: t.complaint_text || "(photo only, no text)" }),
      t.english_translation ? el("div", {},
        el("div", { class: "box-label", text: "English translation" }),
        el("p", { text: t.english_translation })) : null,
      t.photo_urls.length ? el("div", { class: "thumbs" }, ...t.photo_urls.map((url) =>
        el("a", { href: url, target: "_blank", rel: "noopener", class: "thumb" },
          el("img", { src: url, alt: "Complaint photo", loading: "lazy" })))) : null,
      t.image_observations ? el("p", { class: "hint", text: `AI on photos: ${t.image_observations}` }) : null);
  }

  // ----- urgent action requests (with reason + proof, officers only) -----
  function urgencyBox(t) {
    if (!t.urgency_requests.length) return null;
    return el("section", { class: "urgency" },
      el("div", { class: "box-label", text: `⚡ Urgent action requests (${t.urgency_requests.length})` }),
      el("ul", { class: "ur-list" }, ...t.urgency_requests.map((u) => {
        const pending = u.status === "Pending review";
        const note = el("input", { type: "text", maxlength: "300", placeholder: "Note for the citizen (required to reject)" });
        const decide = async (decision) => {
          try {
            await api(`/api/officer/urgency/${u.id}/decide`, { method: "POST", body: { decision, note: note.value } });
            toast(`Urgency request ${decision.toLowerCase()}.`);
            await Promise.all([loadTickets(), loadNotifications()]);
          } catch (err) {
            handleError(err);
          }
        };
        return el("li", { class: `ur-item ${u.status.toLowerCase().replace(/\s+/g, "-")}` },
          el("div", { class: "ur-line" },
            el("span", { class: "ur-status", text: u.status }),
            el("span", { text: `Wants it within ${plural(u.requested_days, "day")}` }),
            el("span", { class: "ur-ai" }, "AI proof check: ",
              el("span", { class: `verdict ${String(u.ai_verdict).toLowerCase().replace(/\s+/g, "-")}`, text: u.ai_verdict })),
            u.from_complainant ? el("span", { class: "tag-complainant", text: "Complainant" }) : null,
            el("span", { class: "hint", text: formatDate(u.created_at) })),
          el("p", { class: "ur-reason" }, el("strong", { text: "Reason: " }), u.reason),
          u.ai_note ? el("p", { class: "hint", text: `AI: ${u.ai_note}` }) : null,
          u.proof_urls.length ? el("div", { class: "thumbs" }, ...u.proof_urls.map((url) =>
            el("a", { href: url, target: "_blank", rel: "noopener", class: "thumb" },
              el("img", { src: url, alt: "Proof photo" })))) : el("p", { class: "hint", text: "(no proof photos stored)" }),
          u.officer_note ? el("p", { class: "ur-officer", text: `Your note: ${u.officer_note}` }) : null,
          pending ? el("div", { class: "ur-decide" },
            note,
            el("button", { type: "button", class: "btn btn-small btn-primary", text: "Accept", onclick: () => decide("Accepted") }),
            el("button", { type: "button", class: "btn btn-small btn-secondary", text: "Reject", onclick: () => decide("Rejected") })) : null);
      })));
  }

  function feedbackBox(t) {
    return el("section", { class: "feedback" },
      el("div", { class: "box-label", text: `Citizen feedback (${t.feedback.length})` }),
      el("ul", { class: "fb-list" }, ...t.feedback.map((f) =>
        el("li", {},
          el("p", { text: f.text }),
          el("div", { class: "fb-meta" },
            el("span", { text: f.from_complainant ? "Complainant" : "Citizen" }),
            el("span", { text: formatDate(f.created_at) }))))));
  }

  // ----- the officer's update: 2 checkboxes + action taken -----
  function updateBox(t) {
    const saved = { inProgress: t.status !== "Confirmed", resolved: t.status === "Resolved", action: t.action_taken || "" };
    const state = drafts.get(t.ticket_id) || saved;

    const inProgress = el("input", { type: "checkbox", checked: state.inProgress });
    const resolved = el("input", { type: "checkbox", checked: state.resolved });
    const action = el("textarea", { rows: "2", maxlength: "2000", placeholder: "Write the action taken (shown to citizens)…" });
    action.value = state.action;
    const error = el("div", { class: "error", role: "alert", hidden: true });
    const submit = el("button", { type: "submit", class: "btn btn-primary btn-small", text: "Submit update" });

    const remember = () => drafts.set(t.ticket_id,
      { inProgress: inProgress.checked, resolved: resolved.checked, action: action.value });
    // Resolved implies In Progress; un-ticking In Progress also un-ticks Resolved
    inProgress.addEventListener("change", () => { if (!inProgress.checked) resolved.checked = false; remember(); });
    resolved.addEventListener("change", () => { if (resolved.checked) inProgress.checked = true; remember(); });
    action.addEventListener("input", remember);

    const form = el("form", {
      class: "off-update",
      onsubmit: async (e) => {
        e.preventDefault();
        const status = resolved.checked ? "Resolved" : inProgress.checked ? "In Progress" : "Confirmed";
        const text = action.value.trim();
        if (status === "Resolved" && !text) {
          showError(error, "Describe the action taken before marking it Resolved.");
          action.focus();
          return;
        }
        submit.disabled = true;
        try {
          await api(`/api/officer/tickets/${t.ticket_id}/update`, { method: "POST", body: { status, action_taken: text } });
          drafts.delete(t.ticket_id);
          toast("Update saved. Citizens can now see it.");
          await Promise.all([loadTickets(), loadNotifications()]);
        } catch (err) {
          if (!handleError(err)) showError(error, err.message);
          submit.disabled = false;
        }
      },
    },
      el("div", { class: "box-label", text: "Your update for citizens" }),
      el("div", { class: "checks" },
        el("label", { class: "check" }, inProgress, el("span", { text: "In Progress" })),
        el("label", { class: "check" }, resolved, el("span", { text: "Resolved" }))),
      action,
      error,
      el("div", { class: "off-update-foot" },
        el("span", { class: "hint", text: t.action_updated_at
          ? `Last update ${formatDate(t.action_updated_at)}${t.action_by ? ` by ${t.action_by}` : ""}`
          : "No update yet - citizens see “Action yet to be taken”." }),
        submit));
    return form;
  }

  $("off-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-filter]");
    if (!b) return;
    filter = b.dataset.filter;
    $("off-filter").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    renderList();
  });

  // ---------------------------------------------------------------------
  // Notifications (bell)
  // ---------------------------------------------------------------------
  const KIND = {
    critical: { icon: "🚨", label: "Emergency · Critical complaint" },
    urgency: { icon: "⚡", label: "Emergency · Urgent action request" },
    overdue: { icon: "🤖", label: "AI reminder · Past deadline" },
  };

  async function loadNotifications() {
    try {
      const data = await api("/api/officer/notifications");
      notifications = data.notifications;
      const count = $("bell-count");
      count.hidden = data.unread === 0;
      count.textContent = data.unread > 99 ? "99+" : data.unread;
      $("bell").classList.toggle("has-unread", data.unread > 0);
      $("bell").setAttribute("aria-label", `Notifications (${data.unread} unread)`);
      renderNotifications();
    } catch (err) {
      handleError(err);
    }
  }

  function renderNotifications() {
    $("notif-list").replaceChildren(...notifications.map((n) => {
      const k = KIND[n.kind] || { icon: "🔔", label: n.kind };
      return el("li", {},
        el("button", {
          type: "button",
          class: "notif" + (n.read ? "" : " unread") + (n.emergency ? " emergency" : ""),
          onclick: () => openNotification(n),
        },
          el("span", { class: "notif-icon", "aria-hidden": "true", text: k.icon }),
          el("span", { class: "notif-body" },
            el("span", { class: "notif-kind", text: k.label }),
            el("span", { class: "notif-msg", text: n.message }),
            el("span", { class: "notif-time", text: formatDate(n.created_at) }))));
    }));
    $("notif-empty").hidden = notifications.length > 0;
  }

  async function openNotification(n) {
    togglePanel(false);
    if (!n.read) {
      api("/api/officer/notifications/read", { method: "POST", body: { ids: [n.id] } })
        .then(loadNotifications).catch(handleError);
    }
    if (!n.ticket_id) return;
    let target = $(`o-${n.ticket_id}`);
    if (!target) { // e.g. it's resolved and the "Open" filter is on
      filter = "all";
      $("off-filter").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.filter === "all"));
      renderList();
      target = $(`o-${n.ticket_id}`);
    }
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.remove("flash");
      void target.offsetWidth; // restart the animation
      target.classList.add("flash");
    }
  }

  function togglePanel(open) {
    $("notif-panel").hidden = !open;
    $("bell").setAttribute("aria-expanded", String(open));
  }

  $("bell").addEventListener("click", (e) => {
    e.stopPropagation();
    togglePanel($("notif-panel").hidden);
  });
  document.addEventListener("click", (e) => {
    if (!$("notif-panel").hidden && !e.target.closest(".bell-wrap")) togglePanel(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") togglePanel(false); });
  $("notif-read-all").addEventListener("click", async () => {
    try {
      await api("/api/officer/notifications/read", { method: "POST", body: { all: true } });
      loadNotifications();
    } catch (err) {
      handleError(err);
    }
  });

  // ---------------------------------------------------------------------
  // Logout + start
  // ---------------------------------------------------------------------
  $("logout").addEventListener("click", async () => {
    try { await api("/api/officer/logout", { method: "POST", body: {} }); } catch { /* ignore */ }
    window.location.href = "/officer/login";
  });

  loadMe();
  loadTickets();
  loadNotifications();
  setInterval(loadNotifications, POLL_MS);
})();
