# CivicLens – AI Triage for Citizen Complaints

A citizen reports a civic problem (text in English or any Indian language, and/or photos).
AI turns it into a structured, prioritised ticket routed to the right municipal department.
The citizen checks the ticket, edits it if needed, and confirms it. It then appears in that
department's public complaint history, where officers post actions and citizens leave feedback.

**Live demo:** `docs/index.html` is an interactive preview that runs entirely in the
browser, using offline demo rules and sample data. Turn on GitHub Pages (see below) to
get a public link.

## Tech stack
- **Backend:** Python + Flask
- **Database:** SQLite (`civiclens.db`, created automatically)
- **Frontend:** plain HTML, CSS, JavaScript (no build step)
- **AI:** Google Gemini or Anthropic Claude (multilingual + reads photos), with an offline demo mode

## Project structure
```
civiclens/
├── app.py              # Flask server: pages + JSON API
├── ai_triage.py        # AI engine: prompt, Gemini/Claude calls, validation, demo mode
├── departments.py      # the 7 departments (name, icon, description, what they handle)
├── database.py         # SQLite: tickets, status, actions, feedback
├── seed_demo.py        # optional: fills the app with sample complaints for a demo
├── docs/index.html     # interactive demo page for GitHub Pages (built by tools/build_preview.py)
├── tools/
│   └── build_preview.py  # rebuilds docs/index.html from the real code
├── requirements.txt
├── .env.example        # copy to .env and add your API key
├── templates/
│   ├── base.html           # shared header + ticket panel
│   ├── _ticket_panel.html  # floating "Your ticket" panel (Edit / Confirm)
│   ├── report.html         # Page 1 - Report an issue
│   ├── departments.html    # Page 2 - Department tabs (+ urgent-action dialog)
│   ├── officer_login.html  # Officer login
│   └── officer.html        # Officer dashboard
├── static/
│   ├── css/style.css
│   └── js/
│       ├── common.js       # helpers + ticket panel logic (every page)
│       ├── report.js       # Page 1
│       ├── departments.js  # Page 2
│       ├── officer-login.js
│       └── officer.js      # officer dashboard + notification bell
└── uploads/            # saved complaint photos
```

## Run it
```bash
cd civiclens
python -m venv venv
# Windows:  venv\Scripts\activate
# Mac/Linux: source venv/bin/activate
pip install -r requirements.txt

copy .env.example .env      # Windows   (Mac/Linux: cp .env.example .env)
# then open .env and paste your GEMINI_API_KEY (free) or ANTHROPIC_API_KEY

python seed_demo.py         # optional: sample complaints so departments aren't empty
python app.py
```
Open http://127.0.0.1:5000

No key? It still runs in **demo mode** (keyword rules). Translation and photo
analysis need a real AI key.

> Note: on Gemini's free tier, Google may use the data you send to improve its
> products. Use test complaints, not real citizens' personal data, until you move
> to a paid tier.

To start over with an empty database: stop the app and delete `civiclens.db`.

## Put it on GitHub
1. Install [Git](https://git-scm.com/downloads) and sign in to GitHub in VS Code
   (Accounts icon, bottom left).
2. Open this folder in VS Code, go to **Source Control** (Ctrl+Shift+G), and click
   **Publish to GitHub**. Choose a public repository.
3. `.env` (your API keys), `civiclens.db` and uploaded photos are listed in `.gitignore`
   and are **not** uploaded. Check that `.env` is not in the list before publishing.

**Live demo with GitHub Pages:** in the repository, open **Settings → Pages →
Build and deployment**, choose **Deploy from a branch**, then branch `main`, folder
`/docs`, and **Save**. After a minute or two the demo is at
`https://<your-username>.github.io/<repo-name>/`.

After you change the app, rebuild the demo and push again:
```bash
python tools/build_preview.py
```

## How it works
1. **Page 1 – Report an issue** (`/`): text and/or up to 4 photos, optional location.
2. AI creates a **Draft** ticket. The citizen is taken to **Page 2 – Departments**
   (`/departments`) with the routed department's tab open.
3. The **Your ticket** panel (bottom-right, minimise/maximise) stays on every page until
   the citizen confirms:
   - **Edit** – change the problem statement/location, remove or add photos; the AI
     re-generates the ticket (and may re-route it).
   - **Confirm** – ticket becomes **Confirmed** and appears in the department's history
     with "Action yet to be taken".
4. Each department tab shows: a short description, stats, the complaint history with
   status (**Confirmed → In Progress → Resolved**), the action taken by the officer,
   and citizen feedback with a text box on every complaint.
5. **⚡ Request urgent action** (on Confirmed / In Progress complaints): a citizen who
   needs it fixed sooner than its severity gives
   - the **reason** it is urgent (at least 15 characters),
   - the **number of days** they need it done in (1–60),
   - **proof** – 1 to 3 photos (the danger itself, or a school/hospital letter). Required.

   The AI compares the proof with the reason and gives an advisory verdict for the
   officer: **Supported / Weak / Not supported** (`Unverified` in demo mode). The request
   stays **Pending review** until an officer **Accepts** or **Rejects** it. Accepted
   requests show a ⚡ Urgent tag on the complaint.
   Everyone sees that urgency was requested (days, status, AI verdict, officer note).
   Only the requester and officers see the reason and proof.

Drafts are private. Only the browser that filed a ticket holds its secret edit token
(stored in `localStorage`). The public history shows the AI summary, not the citizen's
raw text, which might contain names or phone numbers.

## Officer portal
- **Login** (`/officer/login`, also linked from the citizen pages): name, department,
  password. **Prototype: the password is the officer's name** (not case-sensitive).
  Replace this with real accounts before using it anywhere real.
- **Dashboard** (`/officer`): only the officer's own department.
  - Complaints sorted by **severity** (Critical → Low), then by deadline. Resolved last.
  - Each complaint shows its **deadline** (Critical 3 days, High 8, Medium 15, Low 30 from
    confirmation; an accepted urgent-action request can bring it forward), the citizen's
    original text, translation and photos, urgent-action requests (with reason and proof,
    Accept / Reject), and citizen feedback.
  - **Update box** on every complaint: ☐ In Progress ☐ Resolved + **action taken**.
    *Submit update* makes it visible to citizens immediately. Resolved needs an action text.
- **🔔 Notifications** (checked every minute):
  - 🚨 *Emergency:* a Critical complaint was confirmed.
  - ⚡ *Emergency:* a citizen requested urgent action.
  - 🤖 *AI reminder:* a complaint is past its deadline. A new reminder is added every
    day it stays overdue. These reminders are generated automatically from the
    deadlines; no AI model call is involved.
  Notifications are marked read when clicked, when the complaint is resolved, or when an
  urgency request is decided.

Set `FLASK_SECRET_KEY` in `.env` so officer logins survive a server restart.

## API
The edit token goes in the `X-Edit-Token` header.

| method | path | what |
|---|---|---|
| POST | `/api/complaints` | form: `complaint`, `location`, `photos` (0–4 files) → `{ticket, edit_token}` (Draft) |
| GET | `/api/tickets/<id>` | ticket (drafts need the token) |
| POST | `/api/tickets/<id>/edit` | form: `complaint`, `location`, `keep_photos` (names), `photos` (new) – draft only, token |
| POST | `/api/tickets/<id>/confirm` | Draft → Confirmed – token |
| POST | `/api/tickets/<id>/feedback` | JSON `{"text": "..."}`; with the token it is marked "Complainant" |
| POST | `/api/tickets/<id>/urgency` | form: `reason`, `days`, `proof` (1–3 photos) → `{request, request_token}` |
| GET | `/api/urgency/<request_id>` | the requester's own request incl. reason + proof – `X-Request-Token` header |
| GET | `/api/departments` | departments + counts per status |
| GET | `/api/departments/<key>/tickets` | history log of one department (with feedback) |

Officer API (login session cookie; POST bodies must be JSON):

| method | path | what |
|---|---|---|
| POST | `/api/officer/login` | `{name, department, password}` |
| POST | `/api/officer/logout` | |
| GET | `/api/officer/me` | logged-in officer |
| GET | `/api/officer/tickets` | own department, sorted, with deadlines + counts |
| POST | `/api/officer/tickets/<id>/update` | `{status: Confirmed / In Progress / Resolved, action_taken}` |
| POST | `/api/officer/urgency/<request_id>/decide` | `{decision: Accepted / Rejected, note}` |
| GET | `/api/officer/notifications` | emergencies + overdue reminders, unread count |
| POST | `/api/officer/notifications/read` | `{ids: [...]}` or `{all: true}` |

### Ticket fields
| field | values |
|---|---|
| `category` | `roads`, `waste`, `water`, `electricity`, `trees`, `drainage`, `other` |
| `severity` | `Low`, `Medium`, `High`, `Critical` |
| `severity_reason` | one short sentence |
| `department` | routed from category (see table below) |
| `summary` | one-line English summary |
| `detected_language` | e.g. English, Hindi, Tamil, Hinglish |
| `location` | location the citizen mentioned, or `""` |

Extras for the officer: `english_translation`, `image_observations`.
System fields: `ticket_id`, `status`, `created_at`, `confirmed_at`, `photo_urls`,
`action_taken`, `action_updated_at`, `ai_engine`.

**Severity guide:** Critical = immediate danger to life/safety (danger near schools or
hospitals, open manhole on a busy road, tree fallen on a house or blocking a main road) ·
High = serious, affects many or getting worse (water leak, big pothole on main road,
garbage overflowing) · Medium = real but not urgent (one broken streetlight, small
roadside pothole) · Low = minor/maintenance (small litter, overgrown branches not
blocking anything).

### Departments (one per category, set in `departments.py`)
| category | department | handles |
|---|---|---|
| roads | Roads & Engineering Department | potholes, damaged roads, footpaths, speed breakers |
| waste | Solid Waste Management Department | garbage collection, overflowing bins, dumping |
| water | Water Supply Department | supply, pipeline leaks, contaminated water |
| electricity | Street Lighting & Electrical Department | streetlights, poles, hanging wires |
| trees | Horticulture & Tree Cell | fallen trees, dangerous branches, pruning |
| drainage | Drainage & Sewerage Department | blocked drains, sewage, manholes, waterlogging |
| other | Public Grievance Cell | everything else, forwarded manually |
