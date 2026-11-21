"""
Build docs/index.html: a single self-contained, interactive preview of CivicLens
made from the real project files. GitHub Pages can host it as a live demo.

Run from the project folder after changing the app:
    python tools/build_preview.py

The Flask backend is replaced by an in-browser mock server (demo triage rules +
sample data), routing uses #report / #departments, and storage is in memory.
"""
import json
import re
import sys

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project folder
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["AI_PROVIDER"] = "demo"  # the build never calls a real AI

import app as flask_app  # noqa: E402
import ai_triage  # noqa: E402
import seed_demo  # noqa: E402
from departments import DEPARTMENTS  # noqa: E402


def read(path):
    with open(f"{ROOT}/{path}", encoding="utf-8") as fh:
        return fh.read()


def patch(src, old, new, name):
    if src.count(old) != 1:
        raise SystemExit(f"[{name}] patch target found {src.count(old)} times:\n{old[:120]}")
    return src.replace(old, new)


# ---------------------------------------------------------------------------
# HTML pieces, rendered from the real Jinja templates
# ---------------------------------------------------------------------------
client = flask_app.app.test_client()
report_html = client.get("/").get_data(as_text=True)
dept_html = client.get("/departments").get_data(as_text=True)

officer_login_html = client.get("/officer/login").get_data(as_text=True)
with client.session_transaction() as sess:
    sess["officer"] = {"name": "Preview", "department": "roads"}
officer_html = client.get("/officer").get_data(as_text=True)


def grab(pattern, html):
    return re.search(pattern, html, re.S).group(0)


HEADER = r'<header class="site-header">.*?</header>'
header = grab(HEADER, report_html)
header = header.replace('<header class="site-header">', '<header class="site-header" id="hdr-citizen">')
header = header.replace('href="/" class="active"', 'href="#report" data-view="report"')
header = header.replace('href="/departments" class=""', 'href="#departments" data-view="departments"')
header = header.replace('href="/officer/login" class="nav-officer"', 'href="#officer-login" data-view="officer-login" class="nav-officer"')
header = header.replace('<a class="brand" href="/">', '<a class="brand" href="#report">')
assert 'data-view="report"' in header and 'data-view="departments"' in header and "#officer-login" in header, header

hdr_off_login = grab(HEADER, officer_login_html)
hdr_off_login = hdr_off_login.replace('<header class="site-header">', '<header class="site-header" id="hdr-officer-login" hidden>')
hdr_off_login = hdr_off_login.replace('href="/"', 'href="#report"')
hdr_officer = grab(HEADER, officer_html)
hdr_officer = hdr_officer.replace('<header class="site-header">', '<header class="site-header" id="hdr-officer" hidden>')
hdr_officer = hdr_officer.replace('href="/"', 'href="#report"')

off_login_main = grab(r"<main.*?</main>", officer_login_html).replace("<main ", '<main id="view-officer-login" hidden ', 1)
officer_main = grab(r"<main.*?</main>", officer_html).replace("<main ", '<main id="view-officer" hidden ', 1)
toast = grab(r'<div id="toast".*?</div>', officer_html)

report_main = re.search(r"<main.*?</main>", report_html, re.S).group(0)
report_main = report_main.replace("<main ", '<main id="view-report" ', 1)
report_main = patch(report_main, 'href="/officer/login"', 'href="#officer-login"', "report_main")
dept_main = re.search(r"<main.*?</main>", dept_html, re.S).group(0)
dialog = re.search(r'<dialog id="urgency-dialog".*?</dialog>', dept_html, re.S).group(0)
dept_main = dept_main.replace("<main ", '<main id="view-departments" hidden ', 1)
panel = re.search(r'<aside id="ticket-panel".*?</aside>', report_html, re.S).group(0)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
css = read("static/css/style.css") + """
/* preview-only */
body.officer-mode #ticket-panel { display: none !important; }
.preview-bar {
  background: #fff7e0; border-bottom: 1px solid #f0d58a; color: #6b5200;
  font-size: 13px; text-align: center; padding: 7px 16px;
}
"""

# ---------------------------------------------------------------------------
# JS: real files + small patches
# ---------------------------------------------------------------------------
common = read("static/js/common.js")
common = patch(common, """  async function api(url, { method = "GET", body, token, headers: extra = {} } = {}) {
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
  }""", """  async function api(url, { method = "GET", body, token, headers: extra = {} } = {}) {
    return MockServer.handle(url, method, body, token, extra); // preview: no real server
  }""", "common")
common = patch(common, 'el("img", { src: `/uploads/${name}`, alt: "Attached photo" })',
               'el("img", { src: MockServer.photoUrl(name), alt: "Attached photo" })', "common")
common = patch(common,
               '$("tp-view-link").href = `/departments?dept=${encodeURIComponent(ticket.category)}#${ticket.ticket_id}`;',
               '$("tp-view-link").href = "#departments";\n'
               '        $("tp-view-link").onclick = (e) => { e.preventDefault(); Router.go("departments", ticket.category, ticket.ticket_id); };',
               "common")
common = patch(common, "    return { init };\n  })();", "    return { init };\n  })();\n  window.__refreshPanel = () => Panel.init();", "common")

report = read("static/js/report.js")
report = patch(report, """      window.location.href = `/departments?dept=${encodeURIComponent(res.ticket.category)}`;""",
               """      resetForm();
      Router.go("departments", res.ticket.category);
      window.__refreshPanel();""", "report")
report = patch(report, "  // ---------- submit ----------", """  function resetForm() {
    form.reset();
    photos = [];
    renderPreviews();
    $("char-count").textContent = "0";
    showError(errorBox, "");
    $("loading-section").hidden = true;
    $("report-section").hidden = false;
    submitBtn.disabled = false;
  }
  window.ReportPage = {
    onShow() {
      $("pending-banner").hidden = !CL.getDraft();
      if (!$("loading-section").hidden) return;
      $("report-section").hidden = false;
    },
  };

  // ---------- submit ----------""", "report")

dept = read("static/js/departments.js")
dept = patch(dept, "  const params = new URLSearchParams(window.location.search);\n", "", "dept")
dept = patch(dept, """    const url = new URL(window.location);
    url.searchParams.set("dept", key);
    window.history.replaceState(null, "", url);
""", "", "dept")
dept = patch(dept, '    if (params.get("dept")) return;', "    if (Router.dept) return;", "dept")
dept = patch(dept, """  (async () => {
    try {
      await loadDepartments();
      const highlight = window.location.hash ? window.location.hash.slice(1) : null;
      await selectDept(params.get("dept") || pendingDept || departments[0].key, { highlight });
    } catch (err) {""", """  window.DeptPage = { open: async (dept, highlight) => {
    try {
      await loadDepartments();
      await selectDept(dept || current || pendingDept || departments[0].key, { highlight });
    } catch (err) {""", "dept")
dept = patch(dept, """      $("dept-description").textContent = err.message;
    }
  })();""", """      $("dept-description").textContent = err.message;
    }
  } };""", "dept")

off_login = read("static/js/officer-login.js")
off_login = patch(off_login, '      window.location.href = "/officer";', '      Router.go("officer");', "officer-login")
off_login = patch(off_login, '  $("login-form").addEventListener("submit"', '''  window.OfficerLoginPage = {
    onShow() { $("login-form").reset(); showError($("login-error"), ""); $("login-btn").disabled = false; },
  };

  $("login-form").addEventListener("submit"''', "officer-login")

officer = read("static/js/officer.js")
officer = patch(officer, '''      window.location.href = "/officer/login";
      return true;''', '''      Router.go("officer-login");
      return true;''', "officer")
officer = patch(officer, '''    window.location.href = "/officer/login";
  });''', '''    Router.go("officer-login");
  });''', "officer")
officer = patch(officer, '''  loadMe();
  loadTickets();
  loadNotifications();
  setInterval(loadNotifications, POLL_MS);''', '''  window.OfficerPage = { open() { loadMe(); loadTickets(); loadNotifications(); } };
  setInterval(() => { if (location.hash === "#officer") loadNotifications(); }, POLL_MS);''', "officer")

# ---------------------------------------------------------------------------
# Mock server (mirrors app.py + database.py + ai_triage demo rules)
# ---------------------------------------------------------------------------
rules = {
    "departments": DEPARTMENTS,
    "severities": ai_triage.SEVERITIES,
    "keywords": ai_triage._KEYWORDS,
    "weak": sorted(ai_triage._WEAK_KEYWORDS),
    "severityRules": ai_triage._SEVERITY_RULES,
    "danger": ai_triage._DANGER_WORDS,
    "sensitive": ai_triage._SENSITIVE_PLACES,
    "scripts": ai_triage._SCRIPTS,
    "samples": seed_demo.SAMPLES,
    "urgencySamples": {str(k): v for k, v in seed_demo.URGENCY_SAMPLES.items()},
    "sla": __import__("database").SLA_DAYS,
    "rank": __import__("database").SEVERITY_RANK,
}

mock = r"""
const MockServer = (() => {
  const R = __RULES__;
  const PUBLIC = ["Confirmed", "In Progress", "Resolved"];
  const MAX_PHOTOS = 4;
  const tickets = [];
  const photoStore = new Map();
  let nextId = 1, nextFb = 1, nextPhoto = 1, nextUr = 1, nextNotif = 1;
  const DAY = 24 * 3600e3;
  const notifs = [];
  let officer = null; // preview "session"

  function addNotif(dept, kind, t, message, key, urgencyId = null, at = iso(new Date())) {
    if (notifs.some((n) => n.key === key)) return;
    notifs.push({ id: nextNotif++, dept, kind, ticket: t, urgencyId, message, key, created_at: at, read_at: null });
  }
  function markTicketNotifsRead(t) { notifs.forEach((n) => { if (n.ticket === t && !n.read_at) n.read_at = iso(new Date()); }); }
  function deadlineFor(t) {
    if (!t.confirmed_at) return null;
    let d = new Date(new Date(t.confirmed_at).getTime() + R.sla[t.severity] * DAY);
    for (const u of t.urgency)
      if (u.status === "Accepted") d = new Date(Math.min(d, new Date(u.created_at).getTime() + u.requested_days * DAY));
    return d;
  }
  const longDate = (d) => d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  function refreshOverdue(dept) {
    const now = new Date();
    for (const t of tickets) {
      if (t.category !== dept || !["Confirmed", "In Progress"].includes(t.status)) continue;
      const dl = deadlineFor(t);
      if (!dl || now <= dl) continue;
      const over = Math.floor((now - dl) / DAY);
      const late = over === 0 ? "is now overdue" : `is overdue by ${over} day${over !== 1 ? "s" : ""}`;
      addNotif(dept, "overdue", t, `${t.code} (${t.severity}) ${late}. It should have been resolved by ${longDate(dl)}. Current status: ${t.status}.`,
        `overdue:${t.id}:${over}`, null, iso(new Date(dl.getTime() + over * DAY)));
    }
  }
  function setStatus(t, status, action, by, at = iso(new Date())) {
    Object.assign(t, { status, action_taken: action, action_updated_at: at, updated_at: at, action_by: by });
    if (status === "Resolved") markTicketNotifsRead(t);
  }
  function decide(t, u, status, note, at = iso(new Date())) {
    Object.assign(u, { status, officer_note: note, decided_at: at });
    notifs.forEach((n) => { if (n.urgencyId === u.id && !n.read_at) n.read_at = at; });
  }
  function confirmTicket(t, at = iso(new Date())) {
    t.status = "Confirmed"; t.confirmed_at = t.updated_at = at;
    if (t.severity === "Critical") addNotif(t.category, "critical", t, `New CRITICAL complaint ${t.code}: ${t.summary}`, `critical:${t.id}`, null, at);
  }
  function addUrgency(t, u) {
    t.urgency.push(u);
    addNotif(t.category, "urgency", t, `Citizen requested urgent action on ${t.code} within ${u.requested_days} day${u.requested_days !== 1 ? "s" : ""}. AI proof check: ${u.ai_verdict}.`,
      `urgency:${u.id}`, u.id, u.created_at);
  }

  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (v, max) => String(v ?? "").split(/\s+/).filter(Boolean).join(" ").slice(0, max);
  const deptName = (c) => (R.departments[c] || R.departments.other).name;
  function fail(status, message) { const e = new Error(message); e.status = status; throw e; }
  function randomToken() {
    const a = new Uint8Array(18); crypto.getRandomValues(a);
    return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  // ---------------- demo triage (same rules as ai_triage.py demo mode) ----------------
  const LOC = /\b(?:near|opposite|opp\.?|behind|in front of|outside|at|on|in)\s+([A-Z0-9][^,.;!?\n]{2,60})/;
  const LOC_STOP = /\s+(?:for|since|from|and|but|with|is|are|has|have|which|where|people)\b.*$/i;

  function detectLanguage(text) {
    if (!text) return "None";
    for (const [start, end, name] of R.scripts)
      for (const ch of text) if (ch >= start && ch <= end) return name;
    return "English";
  }
  function severityFor(lower) {
    if (R.sensitive.some((p) => lower.includes(p)) && R.danger.some((d) => lower.includes(d)))
      return ["Critical", "Possible danger near a school or hospital."];
    for (const [level, reason, words] of R.severityRules)
      if (words.some((w) => lower.includes(w))) return [level, reason];
    return ["Medium", "Real problem but no sign of immediate danger."];
  }
  function triage(text, photoCount, locationField) {
    const lower = text.toLowerCase();
    let category = "other", best = 0;
    for (const [cat, kws] of Object.entries(R.keywords)) {
      const score = kws.filter((k) => lower.includes(k)).reduce((s, k) => s + (R.weak.includes(k) ? 1 : 3), 0);
      if (score > best) { best = score; category = cat; }
    }
    let [severity, reason] = severityFor(lower);
    if (category === "other" && severity === "Medium") [severity, reason] = ["Low", "Not clearly a civic issue."];
    if (!text) [severity, reason] = ["Medium", "Photo-only complaint; officer should verify severity."];
    const first = text.split("\n")[0];
    const m = text.match(LOC);
    const textLoc = m ? m[1].replace(LOC_STOP, "").trim() : "";
    const language = detectLanguage(text);
    return {
      category, severity, severity_reason: reason + " (demo rules)",
      department: deptName(category),
      summary: clean(text ? (first.length <= 120 ? first : first.slice(0, 117) + "...") : "Photo-only complaint - see attached photos.", 250),
      detected_language: language,
      location: clean([locationField, textLoc].filter(Boolean).join(", "), 300),
      english_translation: ["English", "None"].includes(language) ? "" : "(Translation needs a real AI key - this preview uses the offline demo rules)",
      image_observations: photoCount ? "(Photo analysis needs a real AI key - this preview uses the offline demo rules)" : "",
      ai_engine: "demo (preview)",
    };
  }

  // ---------------- storage ----------------
  function create(ai, text, loc, photos, when = iso(new Date())) {
    const id = nextId++;
    const code = `CL-${when.slice(0, 10).replace(/-/g, "")}-${String(id).padStart(5, "0")}`;
    const token = randomToken();
    tickets.push({ id, code, token, status: "Draft", created_at: when, updated_at: when, confirmed_at: null,
      complaint_text: text, location_input: loc, photos, ...ai, action_taken: "", action_updated_at: null, action_by: "", feedback: [], urgency: [] });
    return { code, token };
  }
  const find = (code) => tickets.find((t) => t.code === code) || fail(404, "Not found");
  const owns = (t, token) => !!token && t.token === token;
  const urls = (t) => t.photos.map((p) => photoStore.get(p));

  function privateView(t) {
    return { ticket_id: t.code, status: t.status, created_at: t.created_at, updated_at: t.updated_at,
      confirmed_at: t.confirmed_at, category: t.category, severity: t.severity, severity_reason: t.severity_reason,
      department: t.department, summary: t.summary, detected_language: t.detected_language, location: t.location,
      english_translation: t.english_translation, image_observations: t.image_observations, ai_engine: t.ai_engine,
      complaint_text: t.complaint_text, location_input: t.location_input, photos: [...t.photos], photo_urls: urls(t),
      action_taken: t.action_taken, action_updated_at: t.action_updated_at };
  }
  function publicView(t) {
    return { ticket_id: t.code, status: t.status, confirmed_at: t.confirmed_at, category: t.category,
      department: t.department, severity: t.severity, severity_reason: t.severity_reason, summary: t.summary,
      detected_language: t.detected_language, location: t.location, photo_urls: urls(t),
      action_taken: t.action_taken, action_updated_at: t.action_updated_at, feedback: t.feedback.map((f) => ({ ...f })),
      urgency_requests: t.urgency.map(urPublic), can_request_urgency: ["Confirmed", "In Progress"].includes(t.status),
      target_date: deadlineFor(t) ? iso(deadlineFor(t)) : null };
  }
  function officerView(t) {
    const dl = deadlineFor(t);
    return { ...privateView(t), action_by: t.action_by, deadline: dl ? iso(dl) : null, sla_days: R.sla[t.severity],
      feedback: t.feedback.map((f) => ({ ...f })), urgency_requests: t.urgency.map((u) => urPrivate(u, t)) };
  }
  const urPublic = (u) => ({ id: u.id, requested_days: u.requested_days, status: u.status, ai_verdict: u.ai_verdict,
    from_complainant: u.from_complainant, officer_note: u.officer_note, created_at: u.created_at, decided_at: u.decided_at });
  const urPrivate = (u, t) => ({ ...urPublic(u), ticket_id: t.code, reason: u.reason,
    proof_urls: u.proof.map((p) => photoStore.get(p)), ai_note: u.ai_note, ai_engine: u.ai_engine });

  async function savePhotos(files) {
    for (const f of files) {
      if (!["image/jpeg", "image/png", "image/webp"].includes(f.type))
        fail(400, "One of the files is not a valid image. Please upload JPG, PNG or WEBP photos.");
      if (f.size > 10 * 1024 * 1024) fail(400, "Each photo must be 10 MB or smaller.");
    }
    const names = [];
    for (const f of files) {
      const dataUrl = await new Promise((res, rej) => {
        const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f);
      });
      const name = `photo-${nextPhoto++}`;
      photoStore.set(name, dataUrl);
      names.push(name);
    }
    return names;
  }
  function readForm(fd) {
    const text = String(fd.get("complaint") || "").trim();
    const loc = String(fd.get("location") || "").trim().slice(0, 300);
    if (text.length > 5000) fail(400, "Complaint is too long (max 5000 characters).");
    const files = fd.getAll("photos").filter((f) => f && f.name);
    return { text, loc, files };
  }

  // ---------------- routes (same as app.py) ----------------
  async function handle(url, method, body, token, headers = {}) {
    await sleep(method === "GET" ? 120 : 350);
    let m;
    if (method === "POST" && url === "/api/complaints") {
      const { text, loc, files } = readForm(body);
      if (!text && !files.length) fail(400, "Please describe the issue or attach a photo - at least one is required.");
      if (files.length > MAX_PHOTOS) fail(400, `You can attach up to ${MAX_PHOTOS} photos.`);
      const photos = await savePhotos(files);
      await sleep(900); // "AI thinking"
      const { code, token: tok } = create(triage(text, photos.length, loc), text, loc, photos);
      return { ticket: privateView(find(code)), edit_token: tok };
    }
    if ((m = url.match(/^\/api\/tickets\/([^/]+)$/)) && method === "GET") {
      const t = find(m[1]);
      if (owns(t, token)) return { ticket: privateView(t) };
      if (t.status === "Draft") fail(404, "Not found");
      return { ticket: publicView(t) };
    }
    if ((m = url.match(/^\/api\/tickets\/([^/]+)\/(edit|confirm|feedback)$/)) && method === "POST") {
      const t = find(m[1]);
      if (m[2] === "feedback") {
        if (t.status === "Draft") fail(404, "Not found");
        const text = clean(body && body.text, 100000);
        if (!text) fail(400, "Feedback can't be empty.");
        if (text.length > 1000) fail(400, "Feedback is too long (max 1000 characters).");
        const fb = { id: nextFb++, text, from_complainant: owns(t, token), created_at: iso(new Date()) };
        t.feedback.push(fb);
        return { feedback: { ...fb } };
      }
      if (!owns(t, token)) fail(403, "Not allowed");
      if (t.status !== "Draft") fail(400, "This ticket is already confirmed and can no longer be edited.");
      if (m[2] === "confirm") {
        confirmTicket(t);
        return { ticket: privateView(t) };
      }
      const { text, loc, files } = readForm(body);
      const keep = body.getAll("keep_photos").filter((p) => t.photos.includes(p));
      if (!text && !keep.length && !files.length) fail(400, "Please describe the issue or keep at least one photo.");
      if (keep.length + files.length > MAX_PHOTOS) fail(400, `A ticket can have at most ${MAX_PHOTOS} photos.`);
      const photos = keep.concat(await savePhotos(files));
      await sleep(900);
      Object.assign(t, triage(text, photos.length, loc), { complaint_text: text, location_input: loc, photos, updated_at: iso(new Date()) });
      return { ticket: privateView(t) };
    }
    if ((m = url.match(/^\/api\/tickets\/([^/]+)\/urgency$/)) && method === "POST") {
      const t = find(m[1]);
      if (t.status === "Draft") fail(404, "Not found");
      if (!["Confirmed", "In Progress"].includes(t.status))
        fail(400, "Urgent action can only be requested on open complaints (Confirmed or In Progress).");
      const reason = clean(body.get("reason"), 100000);
      if (reason.length < 15) fail(400, "Please explain why this is urgent (at least 15 characters).");
      if (reason.length > 1000) fail(400, "Reason is too long (max 1000 characters).");
      const days = Number(body.get("days"));
      if (!Number.isInteger(days)) fail(400, "Enter the number of days (1-60).");
      if (days < 1 || days > 60) fail(400, "Number of days must be between 1 and 60.");
      const files = body.getAll("proof").filter((f) => f && f.name);
      if (!files.length) fail(400, "Proof is required. Upload at least one photo that supports your reason.");
      if (files.length > 3) fail(400, "You can attach up to 3 proof photos.");
      const proof = await savePhotos(files);
      await sleep(900);
      const tok = randomToken();
      const u = { id: nextUr++, token: tok, reason, requested_days: days, proof, from_complainant: owns(t, token),
        ai_verdict: "Unverified", ai_note: "Proof check needs a real AI key - an officer will review the proof manually.",
        ai_engine: "demo (preview)", status: "Pending review", officer_note: "", created_at: iso(new Date()), decided_at: null };
      addUrgency(t, u);
      return { request: urPrivate(u, t), request_token: tok };
    }
    if ((m = url.match(/^\/api\/urgency\/(\d+)$/)) && method === "GET") {
      for (const t of tickets) {
        const u = t.urgency.find((x) => x.id === Number(m[1]));
        if (u && headers["X-Request-Token"] && u.token === headers["X-Request-Token"]) return { request: urPrivate(u, t) };
      }
      fail(404, "Not found");
    }
    // ---------------- officer ----------------
    if (url === "/api/officer/login" && method === "POST") {
      const name = clean(body.name, 1000), dept = String(body.department || ""), pw = String(body.password || "").trim();
      if (name.length < 2 || name.length > 60) fail(400, "Please enter your name (2-60 characters).");
      if (!R.departments[dept]) fail(400, "Please choose your department.");
      if (pw.toLowerCase() !== name.toLowerCase()) fail(401, "Wrong name or password.");
      officer = { name, department: dept };
      return { officer: offInfo() };
    }
    if (url === "/api/officer/logout" && method === "POST") { officer = null; return { ok: true }; }
    if (url.startsWith("/api/officer/")) {
      if (!officer) fail(401, "Please log in as an officer.");
      const dept = officer.department;
      if (url === "/api/officer/me") return { officer: offInfo() };
      if (url === "/api/officer/tickets") {
        const now = new Date();
        const views = tickets.filter((t) => t.category === dept && t.status !== "Draft").map(officerView);
        const key = (v) => v.status === "Resolved"
          ? [1, 0, -new Date(v.action_updated_at || 0).getTime()]
          : [0, R.rank[v.severity], new Date(v.deadline || 0).getTime()];
        views.sort((a, b) => { const x = key(a), y = key(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; });
        const open = views.filter((v) => v.status !== "Resolved");
        return { department: { key: dept, ...R.departments[dept] }, sla_days: R.sla, tickets: views,
          counts: { open: open.length, overdue: open.filter((v) => v.deadline && new Date(v.deadline) < now).length,
            critical: open.filter((v) => v.severity === "Critical").length, resolved: views.length - open.length } };
      }
      if ((m = url.match(/^\/api\/officer\/tickets\/([^/]+)\/update$/))) {
        const t = find(m[1]);
        if (t.category !== dept || t.status === "Draft") fail(404, "Not found");
        const action = String(body.action_taken || "").trim();
        if (!["Confirmed", "In Progress", "Resolved"].includes(body.status)) fail(400, "Invalid status.");
        if (action.length > 2000) fail(400, "Action taken is too long (max 2000 characters).");
        if (body.status === "Resolved" && !action) fail(400, "Describe the action taken before marking the complaint Resolved.");
        setStatus(t, body.status, action, officer.name);
        return { ticket: officerView(t) };
      }
      if ((m = url.match(/^\/api\/officer\/urgency\/(\d+)\/decide$/))) {
        const t = tickets.find((x) => x.category === dept && x.urgency.some((u) => u.id === Number(m[1])));
        if (!t) fail(404, "Not found");
        const u = t.urgency.find((x) => x.id === Number(m[1]));
        if (u.status !== "Pending review") fail(400, "This request has already been decided.");
        const note = clean(body.note, 300);
        if (!["Accepted", "Rejected"].includes(body.decision)) fail(400, "Decision must be Accepted or Rejected.");
        if (body.decision === "Rejected" && !note) fail(400, "Please give a short reason for rejecting the request.");
        decide(t, u, body.decision, note);
        return { ok: true };
      }
      if (url === "/api/officer/notifications" && method === "GET") {
        refreshOverdue(dept);
        const mine = notifs.filter((n) => n.dept === dept)
          .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
        return { unread: mine.filter((n) => !n.read_at).length,
          notifications: mine.slice(0, 50).map((n) => ({ id: n.id, kind: n.kind, emergency: n.kind !== "overdue",
            ticket_id: n.ticket ? n.ticket.code : null, message: n.message, created_at: n.created_at, read: !!n.read_at })) };
      }
      if (url === "/api/officer/notifications/read" && method === "POST") {
        const ids = body.all ? null : (body.ids || []).map(Number);
        notifs.forEach((n) => { if (n.dept === dept && !n.read_at && (!ids || ids.includes(n.id))) n.read_at = iso(new Date()); });
        return { ok: true };
      }
      fail(404, "Not found");
    }
    if (url === "/api/departments" && method === "GET") {
      return { departments: Object.entries(R.departments).map(([key, info]) => {
        const counts = { "Confirmed": 0, "In Progress": 0, "Resolved": 0, total: 0 };
        tickets.filter((t) => t.category === key && t.status !== "Draft").forEach((t) => { counts[t.status]++; counts.total++; });
        return { key, ...info, counts };
      }) };
    }
    if ((m = url.match(/^\/api\/departments\/([^/]+)\/tickets$/)) && method === "GET") {
      const key = m[1];
      if (!R.departments[key]) fail(404, "Not found");
      const list = tickets.filter((t) => t.category === key && t.status !== "Draft")
        .sort((a, b) => (b.confirmed_at || "").localeCompare(a.confirmed_at || "") || b.id - a.id);
      return { department: { key, ...R.departments[key] }, tickets: list.map(publicView) };
    }
    fail(404, "Not found");
  }

  function offInfo() {
    const d = R.departments[officer.department];
    return { ...officer, department_name: d.name, department_icon: d.icon };
  }

  // ---------------- sample data (same as seed_demo.py) ----------------
  (function seed() {
    const now = new Date();
    R.samples.forEach(([days, cat, sev, reason, summary, lang, loc, status, action, feedback], index) => {
      const filed = new Date(now.getTime() - (days * 24 + 3) * 3600e3);
      const ai = { category: cat, severity: sev, severity_reason: reason, department: deptName(cat), summary,
        detected_language: lang, location: loc, english_translation: "", image_observations: "", ai_engine: "sample data" };
      const { code } = create(ai, summary, loc, [], iso(filed));
      const t = find(code);
      confirmTicket(t, iso(new Date(filed.getTime() + 120e3)));
      feedback.forEach((text, i) => t.feedback.push({ id: nextFb++, text, from_complainant: i === 0,
        created_at: iso(new Date(Math.min(filed.getTime() + (24 + i) * 3600e3, now.getTime()))) }));
      (R.urgencySamples[String(index)] || []).forEach(([reason, wanted, verdict, note, urStatus, officerNote]) => {
        const u = { id: nextUr++, token: randomToken(), reason, requested_days: wanted, proof: [],
          from_complainant: false, ai_verdict: verdict, ai_note: note, ai_engine: "sample data", status: "Pending review",
          officer_note: "", created_at: iso(new Date(Math.min(filed.getTime() + 6 * 3600e3, now.getTime()))), decided_at: null };
        addUrgency(t, u);
        if (urStatus !== "Pending review")
          decide(t, u, urStatus, officerNote, iso(new Date(Math.min(filed.getTime() + 9 * 3600e3, now.getTime()))));
      });
      if (status !== "Confirmed")
        setStatus(t, status, action, "Demo Officer", iso(new Date(Math.min(filed.getTime() + 20 * 3600e3, now.getTime()))));
    });
  })();

  return { handle, photoUrl: (name) => photoStore.get(name) || "", isOfficer: () => !!officer };
})();
""".replace("__RULES__", json.dumps(rules, ensure_ascii=False))

router = r"""
const Router = {
  dept: null,
  highlight: null,
  go(view, dept = null, highlight = null) {
    this.dept = dept;
    this.highlight = highlight;
    if (location.hash !== "#" + view) location.hash = view;
    else this.render();
  },
  views: {
    report: { main: "view-report", header: "hdr-citizen", title: "Report an issue" },
    departments: { main: "view-departments", header: "hdr-citizen", title: "Departments" },
    "officer-login": { main: "view-officer-login", header: "hdr-officer-login", title: "Officer login" },
    officer: { main: "view-officer", header: "hdr-officer", title: "Officer dashboard" },
  },
  render() {
    let view = location.hash.slice(1);
    if (!this.views[view]) view = "report";
    if (view === "officer" && !MockServer.isOfficer()) return this.go("officer-login");
    if (view === "officer-login" && MockServer.isOfficer()) return this.go("officer");
    for (const [name, v] of Object.entries(this.views)) document.getElementById(v.main).hidden = name !== view;
    for (const h of ["hdr-citizen", "hdr-officer-login", "hdr-officer"])
      document.getElementById(h).hidden = h !== this.views[view].header;
    document.body.classList.toggle("officer-mode", view.startsWith("officer"));
    document.querySelectorAll(".site-nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
    document.title = this.views[view].title + " · CivicLens";
    window.scrollTo(0, 0);
    if (view === "departments") DeptPage.open(this.dept, this.highlight);
    else if (view === "report") ReportPage.onShow();
    else if (view === "officer-login") OfficerLoginPage.onShow();
    else OfficerPage.open();
    this.highlight = null;
  },
};
window.addEventListener("hashchange", () => Router.render());
document.querySelectorAll(".site-nav a, a.brand").forEach((a) =>
  a.addEventListener("click", () => { Router.dept = null; }));
Router.render();
"""

storage_shim = r"""
// Preview: keep everything in memory (a reload starts fresh)
const localStorage = (() => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
})();
"""

script = ("(() => {\n" + storage_shim + mock + common + "\n" + report + "\n" + dept + "\n"
          + off_login + "\n" + officer + "\n" + router + "\n})();")
# keep "</script>" sequences out of inline JS
script = script.replace("</", "<\\/")

page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CivicLens – Preview</title>
<style>
{css}
</style>
</head>
<body>
{header}
{hdr_off_login}
{hdr_officer}
<div class="preview-bar">Interactive preview. Runs entirely in your browser with the offline demo rules (no real AI). Nothing is saved; reloading starts fresh. Officer password = officer name.</div>
{report_main}
{dept_main}
{off_login_main}
{officer_main}
{dialog}
{toast}
{panel}
<script>
{script}
</script>
</body>
</html>
"""

out = os.path.join(ROOT, "docs", "index.html")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    fh.write(page)
print("wrote", out, len(page), "bytes")
