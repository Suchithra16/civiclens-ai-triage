// CivicLens - Page 2 "Departments" (sidebar tabs, history log, actions, feedback)
(() => {
  const { el, api, severityBadge, statusPill, formatDate, showError } = CL;
  const $ = (id) => document.getElementById(id);

  const params = new URLSearchParams(window.location.search);
  let departments = [];
  let current = null;
  let tickets = [];
  let filter = "all";
  let pendingDept = null; // department chosen before the sidebar finished loading
  const openRequests = new Set();    // urgency request ids whose details are expanded
  const requestDetails = new Map();  // id -> private details (reason, proof) of my requests

  // ---------------------------------------------------------------------
  // Sidebar
  // ---------------------------------------------------------------------
  async function loadDepartments() {
    const data = await api("/api/departments");
    departments = data.departments;
    renderTabs();
  }

  function renderTabs() {
    $("dept-tabs").replaceChildren(
      ...departments.map((d) =>
        el("button", {
          type: "button",
          role: "tab",
          class: "dept-tab" + (d.key === current ? " active" : ""),
          "aria-selected": d.key === current ? "true" : "false",
          onclick: () => selectDept(d.key),
        },
          el("span", { class: "tab-icon", "aria-hidden": "true", text: d.icon }),
          el("span", { class: "tab-name", text: d.name }),
          el("span", { class: "tab-count", title: "Complaints", text: d.counts.total })))
    );
    // on phones the tabs are a horizontal strip - scroll the active one into view
    const strip = $("dept-tabs");
    const active = strip.querySelector(".active");
    if (active && strip.scrollWidth > strip.clientWidth) {
      strip.scrollLeft = active.offsetLeft - (strip.clientWidth - active.clientWidth) / 2;
    }
  }

  // ---------------------------------------------------------------------
  // Selected department
  // ---------------------------------------------------------------------
  async function selectDept(key, { highlight = null } = {}) {
    if (!departments.length) { pendingDept = key; return; }
    if (!departments.some((d) => d.key === key)) key = departments[0].key;
    current = key;
    renderTabs();
    const url = new URL(window.location);
    url.searchParams.set("dept", key);
    window.history.replaceState(null, "", url);

    const data = await api(`/api/departments/${key}/tickets`);
    if (current !== key) return; // user clicked another tab meanwhile
    tickets = data.tickets;
    renderIntro(departments.find((d) => d.key === key));
    renderLog();

    if (highlight) {
      const card = document.getElementById(`t-${highlight}`);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("flash");
      }
    }
  }

  function renderIntro(d) {
    $("dept-icon").textContent = d.icon;
    $("dept-name").textContent = d.name;
    $("dept-description").textContent = d.description;
    $("dept-handles").replaceChildren(el("span", { class: "chips-label", text: "Handles:" }),
      ...d.handles.map((h) => el("span", { class: "chip", text: h })));

    const c = d.counts;
    const stat = (label, value, cls) =>
      el("div", { class: `stat ${cls}` }, el("div", { class: "stat-value", text: value }), el("div", { class: "stat-label", text: label }));
    $("dept-stats").replaceChildren(
      stat("Total", c.total, "total"),
      stat("Confirmed", c["Confirmed"], "confirmed"),
      stat("In Progress", c["In Progress"], "in-progress"),
      stat("Resolved", c["Resolved"], "resolved"),
    );
  }

  // ---------------------------------------------------------------------
  // History log
  // ---------------------------------------------------------------------
  function renderLog() {
    const shown = tickets.filter((t) => filter === "all" || t.status === filter);
    $("ticket-log").replaceChildren(...shown.map(ticketCard));
    $("log-empty").hidden = shown.length > 0;
    $("log-empty").textContent = tickets.length ? "No complaints with this status." : "No complaints here yet.";
  }

  function ticketCard(t) {
    const mine = Boolean(CL.tokenFor(t.ticket_id));

    const actionBox = t.action_taken
      ? el("div", { class: "action done" },
          el("div", { class: "box-label", text: "Action taken" }),
          el("p", { text: t.action_taken }),
          el("div", { class: "hint", text: `Updated ${formatDate(t.action_updated_at)}` }))
      : el("div", { class: "action pending" },
          el("div", { class: "box-label", text: "Action taken" }),
          el("p", { text: "⏳ Action yet to be taken" }));

    const urgentAccepted = t.urgency_requests.some((u) => u.status === "Accepted");

    return el("article", { class: "ticket-card" + (mine ? " mine" : ""), id: `t-${t.ticket_id}` },
      el("header", { class: "tc-head" },
        el("span", { class: "mono tc-id", text: t.ticket_id }),
        mine ? el("span", { class: "tag-mine", text: "Your complaint" }) : null,
        el("span", { class: "spacer" }),
        urgentAccepted ? el("span", { class: "tag-urgent", title: "Urgent action approved by an officer", text: "⚡ Urgent" }) : null,
        statusPill(t.status)),

      el("h3", { class: "tc-summary", text: t.summary }),

      el("div", { class: "tc-meta" },
        severityBadge(t.severity),
        t.location ? el("span", { text: `📍 ${t.location}` }) : null,
        el("span", { text: `🗓 ${formatDate(t.confirmed_at)}` }),
        t.status !== "Resolved" && t.target_date ? targetChip(t.target_date) : null,
        t.detected_language && t.detected_language !== "None"
          ? el("span", { text: `🌐 ${t.detected_language}` }) : null),

      t.severity_reason ? el("p", { class: "tc-reason", text: t.severity_reason }) : null,

      t.photo_urls.length
        ? el("div", { class: "thumbs" }, ...t.photo_urls.map((url) =>
            el("a", { href: url, target: "_blank", rel: "noopener", class: "thumb" },
              el("img", { src: url, alt: "Complaint photo", loading: "lazy" }))))
        : null,

      actionBox,
      urgencySection(t),
      feedbackSection(t),
    );
  }

  function refreshCard(t) {
    const fresh = ticketCard(t);
    const old = document.getElementById(`t-${t.ticket_id}`);
    if (old) old.replaceWith(fresh);
    return fresh;
  }

  // ---------------------------------------------------------------------
  // "Request urgent action" - list on each card
  // ---------------------------------------------------------------------
  const verdictPill = (v) => el("span", { class: `verdict ${String(v || "").toLowerCase().replace(/\s+/g, "-")}`, text: v || "Unverified" });

  function urgencySection(t) {
    const requests = t.urgency_requests;
    const myPending = requests.some((u) => CL.requestTokenFor(u.id) && u.status === "Pending review");
    if (!requests.length && !t.can_request_urgency) return null;

    const items = requests.map((u) => {
      const isMine = Boolean(CL.requestTokenFor(u.id));
      const details = requestDetails.get(u.id);
      const expanded = isMine && openRequests.has(u.id) && details;
      return el("li", { class: `ur-item ${u.status.toLowerCase().replace(/\s+/g, "-")}` },
        el("div", { class: "ur-line" },
          el("span", { class: "ur-status", text: u.status }),
          el("span", { text: `Wanted within ${u.requested_days} day${u.requested_days === 1 ? "" : "s"}` }),
          el("span", { class: "ur-ai" }, "AI proof check: ", verdictPill(u.ai_verdict)),
          isMine ? el("span", { class: "tag-mine", text: "Your request" }) : null,
          u.from_complainant ? el("span", { class: "tag-complainant", text: "Complainant" }) : null,
          el("span", { class: "hint", text: formatDate(u.created_at) })),
        u.officer_note ? el("p", { class: "ur-officer", text: `Officer: ${u.officer_note}` }) : null,
        isMine
          ? el("button", {
              type: "button", class: "btn-link-plain",
              text: expanded ? "Hide my reason & proof" : "Show my reason & proof",
              onclick: () => toggleMyRequest(t, u.id),
            })
          : null,
        expanded
          ? el("div", { class: "ur-details" },
              el("div", { class: "box-label", text: "Your reason" }),
              el("p", { text: details.reason }),
              details.proof_urls.length
                ? el("div", { class: "thumbs" }, ...details.proof_urls.map((url) =>
                    el("a", { href: url, target: "_blank", rel: "noopener", class: "thumb" },
                      el("img", { src: url, alt: "Proof photo" }))))
                : null,
              details.ai_note ? el("p", { class: "hint", text: `AI note: ${details.ai_note}` }) : null)
          : null);
    });

    return el("section", { class: "urgency" },
      requests.length
        ? el("div", { class: "box-label", text: `⚡ Urgent action requests (${requests.length})` })
        : null,
      requests.length ? el("ul", { class: "ur-list" }, ...items) : null,
      t.can_request_urgency && !myPending
        ? el("button", { type: "button", class: "btn btn-urgent-outline btn-small", text: "⚡ Request urgent action",
            onclick: () => Urgency.open(t) })
        : null,
      myPending ? el("p", { class: "hint", text: "Your urgency request is waiting for officer review." }) : null);
  }

  async function toggleMyRequest(t, id) {
    if (openRequests.has(id)) {
      openRequests.delete(id);
    } else {
      if (!requestDetails.has(id)) {
        try {
          const data = await api(`/api/urgency/${id}`, { headers: { "X-Request-Token": CL.requestTokenFor(id) } });
          requestDetails.set(id, data.request);
        } catch (err) {
          return;
        }
      }
      openRequests.add(id);
    }
    refreshCard(t);
  }

  // ---------------------------------------------------------------------
  // "Request urgent action" - dialog
  // ---------------------------------------------------------------------
  const Urgency = (() => {
    const dlg = $("urgency-dialog");
    const MAX_PROOF = 3;
    let ticket = null;
    let proof = [];
    let busy = false;

    function only(id) {
      ["ur-form", "ur-loading", "ur-result"].forEach((x) => ($(x).hidden = x !== id));
    }

    function renderProof() {
      $("ur-proof-previews").replaceChildren(...proof.map((file) =>
        el("div", { class: "thumb" },
          el("img", { src: URL.createObjectURL(file), alt: file.name }),
          el("button", {
            type: "button", class: "thumb-remove", "aria-label": `Remove ${file.name}`, text: "×",
            onclick: () => { proof = proof.filter((f) => f !== file); renderProof(); },
          }))));
      $("ur-add-label").hidden = proof.length >= MAX_PROOF;
    }

    function open(t) {
      ticket = t;
      proof = [];
      $("ur-form").reset();
      $("ur-count").textContent = "0";
      renderProof();
      showError($("ur-error"), "");
      $("ur-ticket-id").textContent = t.ticket_id;
      $("ur-ticket-sev").replaceChildren(severityBadge(t.severity));
      $("ur-ticket-summary").textContent = t.summary;
      only("ur-form");
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
      $("ur-reason").focus();
    }

    function close() {
      if (busy) return;
      if (dlg.close) dlg.close(); else dlg.removeAttribute("open");
    }

    dlg.addEventListener("cancel", (e) => { if (busy) e.preventDefault(); });
    dlg.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) close();
      else if (e.target === dlg) close(); // click on the backdrop
    });
    $("ur-reason").addEventListener("input", () => ($("ur-count").textContent = $("ur-reason").value.length));
    dlg.querySelectorAll("[data-days]").forEach((b) =>
      b.addEventListener("click", () => ($("ur-days").value = b.dataset.days)));
    $("ur-proof").addEventListener("change", (e) => {
      const { accepted, error } = CL.checkPhotos([...e.target.files], proof.length, MAX_PROOF);
      proof.push(...accepted);
      e.target.value = "";
      showError($("ur-error"), error);
      renderProof();
    });

    $("ur-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const reason = $("ur-reason").value.trim();
      const days = Number($("ur-days").value);
      if (reason.length < 15) return showError($("ur-error"), "Please explain why this is urgent (at least 15 characters).");
      if (!Number.isInteger(days) || days < 1 || days > 60) return showError($("ur-error"), "Enter the number of days (1-60).");
      if (!proof.length) return showError($("ur-error"), "Proof is required. Add at least one photo that supports your reason.");

      const form = new FormData();
      form.append("reason", reason);
      form.append("days", String(days));
      proof.forEach((f) => form.append("proof", f));

      busy = true;
      only("ur-loading");
      try {
        const data = await api(`/api/tickets/${ticket.ticket_id}/urgency`, {
          method: "POST", body: form, token: CL.tokenFor(ticket.ticket_id),
        });
        const r = data.request;
        CL.addMyRequest(r.id, data.request_token);
        requestDetails.set(r.id, r);

        $("ur-res-days").textContent = `${r.requested_days} day${r.requested_days === 1 ? "" : "s"}`;
        $("ur-res-verdict").replaceWith(Object.assign(verdictPill(r.ai_verdict), { id: "ur-res-verdict" }));
        $("ur-res-note").textContent = r.ai_note;
        only("ur-result");

        const { reason: _r, proof_urls: _p, ai_note: _n, ai_engine: _e, ticket_id: _t, ...pub } = r;
        ticket.urgency_requests.push(pub);
        refreshCard(ticket);
      } catch (err) {
        only("ur-form");
        showError($("ur-error"), err.message);
      } finally {
        busy = false;
      }
    });

    return { open };
  })();

  // When the department should fix it by (severity deadline, or sooner if urgency was accepted)
  function targetChip(iso) {
    const d = new Date(iso);
    const date = d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    return d < new Date()
      ? el("span", { class: "target late", text: `⏰ Past target date (${date})` })
      : el("span", { class: "target", text: `🎯 Target: ${date}` });
  }

  function feedbackSection(t) {
    const list = el("ul", { class: "fb-list" },
      ...t.feedback.map((f) =>
        el("li", {},
          el("p", { text: f.text }),
          el("div", { class: "fb-meta" },
            f.from_complainant ? el("span", { class: "tag-complainant", text: "Complainant" }) : el("span", { text: "Citizen" }),
            el("span", { text: formatDate(f.created_at) })))));

    const input = el("input", { type: "text", maxlength: "1000", placeholder: "Write your feedback on this complaint…", "aria-label": "Feedback" });
    const error = el("div", { class: "error", role: "alert", hidden: true });
    const button = el("button", { type: "submit", class: "btn btn-primary btn-small", text: "Send" });

    const form = el("form", {
      class: "fb-form",
      onsubmit: async (e) => {
        e.preventDefault();
        const text = input.value.trim();
        if (!text) { showError(error, "Feedback can't be empty."); return; }
        button.disabled = true;
        try {
          const data = await api(`/api/tickets/${t.ticket_id}/feedback`, {
            method: "POST", body: { text }, token: CL.tokenFor(t.ticket_id),
          });
          t.feedback.push(data.feedback);
          refreshCard(t).querySelector(".fb-form input").focus();
        } catch (err) {
          showError(error, err.message);
          button.disabled = false;
        }
      },
    }, input, button);

    return el("section", { class: "feedback" },
      el("div", { class: "box-label", text: `Citizen feedback (${t.feedback.length})` }),
      t.feedback.length ? list : null,
      form,
      error);
  }

  // ---------------------------------------------------------------------
  // Status filter
  // ---------------------------------------------------------------------
  $("status-filter").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-status]");
    if (!btn) return;
    filter = btn.dataset.status;
    $("status-filter").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    renderLog();
  });

  // ---------------------------------------------------------------------
  // Events from the ticket panel
  // ---------------------------------------------------------------------
  window.addEventListener("civiclens:panel-ready", (e) => {
    // came here without ?dept -> open the tab the pending ticket was routed to
    if (params.get("dept")) return;
    if (!departments.length) pendingDept = e.detail.ticket.category;
    else if (e.detail.ticket.category !== current) selectDept(e.detail.ticket.category);
  });

  window.addEventListener("civiclens:ticket-updated", (e) => {
    // after an edit the AI may route the ticket to a different department
    if (e.detail.ticket.category !== current) selectDept(e.detail.ticket.category);
  });

  window.addEventListener("civiclens:ticket-confirmed", async (e) => {
    await loadDepartments(); // refresh counts
    selectDept(e.detail.ticket.category, { highlight: e.detail.ticket.ticket_id });
  });

  // ---------------------------------------------------------------------
  // Start
  // ---------------------------------------------------------------------
  (async () => {
    try {
      await loadDepartments();
      const highlight = window.location.hash ? window.location.hash.slice(1) : null;
      await selectDept(params.get("dept") || pendingDept || departments[0].key, { highlight });
    } catch (err) {
      $("dept-name").textContent = "Could not load departments";
      $("dept-description").textContent = err.message;
    }
  })();
})();
