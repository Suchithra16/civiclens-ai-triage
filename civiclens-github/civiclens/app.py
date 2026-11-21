"""
CivicLens - AI Triage for Citizen Complaints
Run:  python app.py   then open http://127.0.0.1:5000

Pages
    /               Page 1 - Report an issue
    /departments    Page 2 - Department tabs (history log, actions, feedback)
    /officer/login  Officer login
    /officer        Officer dashboard (own department only)

Citizen API
    POST /api/complaints                    create a Draft ticket (AI triage)
    GET  /api/tickets/<code>                read a ticket (drafts need the edit token)
    POST /api/tickets/<code>/edit           change text/location/photos, re-run AI (draft only)
    POST /api/tickets/<code>/confirm        Draft -> Confirmed
    POST /api/tickets/<code>/feedback       add citizen feedback (confirmed tickets)
    POST /api/tickets/<code>/urgency        "Request urgent action": reason + days + proof photos
    GET  /api/urgency/<id>                  the requester's own urgency request (needs its token)
    GET  /api/departments                   all departments + complaint counts
    GET  /api/departments/<key>/tickets     history log of one department

Officer API (needs an officer login session; POSTs must be JSON)
    POST /api/officer/login                 {name, department, password}
    POST /api/officer/logout
    GET  /api/officer/me
    GET  /api/officer/tickets               own department, sorted by severity then deadline
    POST /api/officer/tickets/<code>/update {status, action_taken}
    POST /api/officer/urgency/<id>/decide   {decision: Accepted|Rejected, note}
    GET  /api/officer/notifications         emergencies + overdue reminders
    POST /api/officer/notifications/read    {ids: [...]} or {all: true}

The ticket edit token is sent in the "X-Edit-Token" header,
an urgency request token in the "X-Request-Token" header.
"""

import io
import logging
import os
import secrets
import uuid
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session)
from PIL import Image, ImageOps, UnidentifiedImageError

load_dotenv()  # read API keys etc. from .env

import database as db  # noqa: E402  (after load_dotenv on purpose)
from ai_triage import check_urgency, triage  # noqa: E402
from departments import CATEGORIES, DEPARTMENTS  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MAX_TEXT_CHARS = 5000
MAX_FEEDBACK_CHARS = 1000
MAX_PHOTOS = 4          # per ticket
MAX_PROOF_PHOTOS = 3    # per urgency request
MIN_REASON_CHARS = 15
MAX_REASON_CHARS = 1000
MAX_URGENCY_DAYS = 60
MAX_IMAGE_SIDE = 1600   # px - big phone photos are shrunk before AI sees them

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 45 * 1024 * 1024  # 4 photos x 10 MB + text
# Signs the officer login cookie. Set FLASK_SECRET_KEY in .env so logins survive a restart.
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
logging.basicConfig(level=logging.INFO)

os.makedirs(UPLOAD_DIR, exist_ok=True)
db.init_db()


class BadRequest(Exception):
    """Raised for invalid citizen input -> 400 with a friendly message."""


@app.errorhandler(BadRequest)
def bad_request(err):
    return jsonify(error=str(err)), 400


@app.errorhandler(413)
def too_large(_err):
    return jsonify(error="Upload is too large (max 10 MB per photo)."), 413


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def report_page():
    return render_template("report.html", active="report")


@app.get("/departments")
def departments_page():
    return render_template("departments.html", active="departments")


@app.get("/officer/login")
def officer_login_page():
    if _officer():
        return redirect("/officer")
    return render_template("officer_login.html", active="officer", departments=DEPARTMENTS)


@app.get("/officer")
def officer_page():
    if not _officer():
        return redirect("/officer/login")
    return render_template("officer.html", active="officer")


@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ---------------------------------------------------------------------------
# Citizen API - tickets
# ---------------------------------------------------------------------------

@app.post("/api/complaints")
def create_complaint():
    text, location = _read_text_fields()
    new_files = _uploaded_files()
    if not text and not new_files:
        raise BadRequest("Please describe the issue or attach a photo - at least one is required.")
    if len(new_files) > MAX_PHOTOS:
        raise BadRequest(f"You can attach up to {MAX_PHOTOS} photos.")

    photos = _save_photos(new_files)
    ai = triage(text=text, images=_load_photos(photos), location=location)
    code, token = db.create_ticket(ai, text, location, photos)

    return jsonify(ticket=_private_view(db.get_ticket_row(code)), edit_token=token), 201


@app.get("/api/tickets/<code>")
def read_ticket(code):
    row = _get_row_or_404(code)
    if db.token_matches(row, _token()):
        return jsonify(ticket=_private_view(row))
    if row["status"] == db.STATUS_DRAFT:  # drafts are private to the citizen who filed them
        abort(404)
    return jsonify(ticket=_public_view(row, db.ticket_feedback(row), db.ticket_urgency_requests(row)))


@app.post("/api/tickets/<code>/edit")
def edit_ticket(code):
    row = _require_owned_draft(code)
    text, location = _read_text_fields()

    old_photos = db.photo_list(row)
    keep = [p for p in request.form.getlist("keep_photos") if p in old_photos]
    new_files = _uploaded_files()

    if not text and not keep and not new_files:
        raise BadRequest("Please describe the issue or keep at least one photo.")
    if len(keep) + len(new_files) > MAX_PHOTOS:
        raise BadRequest(f"A ticket can have at most {MAX_PHOTOS} photos.")

    photos = keep + _save_photos(new_files)
    ai = triage(text=text, images=_load_photos(photos), location=location)
    db.update_draft(code, ai, text, location, photos)

    for removed in set(old_photos) - set(keep):
        _delete_photo(removed)

    return jsonify(ticket=_private_view(db.get_ticket_row(code)))


@app.post("/api/tickets/<code>/confirm")
def confirm_ticket(code):
    _require_owned_draft(code)
    db.confirm_ticket(code)
    return jsonify(ticket=_private_view(db.get_ticket_row(code)))


@app.post("/api/tickets/<code>/feedback")
def add_feedback(code):
    row = _get_row_or_404(code)
    if row["status"] == db.STATUS_DRAFT:
        abort(404)

    payload = request.get_json(silent=True) or {}
    text = " ".join(str(payload.get("text", "")).split())
    if not text:
        raise BadRequest("Feedback can't be empty.")
    if len(text) > MAX_FEEDBACK_CHARS:
        raise BadRequest(f"Feedback is too long (max {MAX_FEEDBACK_CHARS} characters).")

    fb = db.add_feedback(row, text, from_complainant=db.token_matches(row, _token()))
    return jsonify(feedback=fb), 201


# ---------------------------------------------------------------------------
# Citizen API - "Request urgent action"
# ---------------------------------------------------------------------------

@app.post("/api/tickets/<code>/urgency")
def request_urgency(code):
    row = _get_row_or_404(code)
    if row["status"] == db.STATUS_DRAFT:
        abort(404)
    if row["status"] not in db.URGENCY_OPEN_STATUSES:
        raise BadRequest("Urgent action can only be requested on open complaints (Confirmed or In Progress).")

    reason = " ".join((request.form.get("reason") or "").split())
    if len(reason) < MIN_REASON_CHARS:
        raise BadRequest(f"Please explain why this is urgent (at least {MIN_REASON_CHARS} characters).")
    if len(reason) > MAX_REASON_CHARS:
        raise BadRequest(f"Reason is too long (max {MAX_REASON_CHARS} characters).")

    try:
        days = int(request.form.get("days", ""))
    except ValueError:
        raise BadRequest(f"Enter the number of days (1-{MAX_URGENCY_DAYS}).")
    if not 1 <= days <= MAX_URGENCY_DAYS:
        raise BadRequest(f"Number of days must be between 1 and {MAX_URGENCY_DAYS}.")

    files = _uploaded_files("proof")
    if not files:
        raise BadRequest("Proof is required. Upload at least one photo that supports your reason.")
    if len(files) > MAX_PROOF_PHOTOS:
        raise BadRequest(f"You can attach up to {MAX_PROOF_PHOTOS} proof photos.")

    proof = _save_photos(files)
    check = check_urgency(dict(row), reason, days, _load_photos(proof))
    request_id, token = db.create_urgency_request(
        row, reason, days, proof, check, from_complainant=db.token_matches(row, _token())
    )
    return jsonify(request=_urgency_private(db.get_urgency_row(request_id)), request_token=token), 201


@app.get("/api/urgency/<int:request_id>")
def read_urgency(request_id):
    row = db.get_urgency_row(request_id)
    if not db.token_matches(row, request.headers.get("X-Request-Token", ""), "token_hash"):
        abort(404)
    return jsonify(request=_urgency_private(row))


# ---------------------------------------------------------------------------
# Citizen API - departments
# ---------------------------------------------------------------------------

@app.get("/api/departments")
def list_departments():
    counts = db.department_counts()
    empty = {s: 0 for s in db.PUBLIC_STATUSES} | {"total": 0}
    return jsonify(departments=[
        {"key": key, **info, "counts": counts.get(key, empty)}
        for key, info in DEPARTMENTS.items()
    ])


@app.get("/api/departments/<key>/tickets")
def department_log(key):
    if key not in CATEGORIES:
        abort(404)
    return jsonify(
        department={"key": key, **DEPARTMENTS[key]},
        tickets=[_public_view(t, fb, ur) for t, fb, ur in db.department_tickets(key)],
    )


# ---------------------------------------------------------------------------
# Officer API
# ---------------------------------------------------------------------------

def _officer():
    """The logged-in officer {"name", "department"} or None."""
    officer = session.get("officer")
    if officer and officer.get("department") in DEPARTMENTS:
        return officer
    return None


def officer_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _officer():
            return jsonify(error="Please log in as an officer."), 401
        # Only accept JSON for changes: a plain cross-site form can't send it (basic CSRF guard)
        if request.method == "POST" and not request.is_json:
            raise BadRequest("Expected a JSON request.")
        return view(*args, **kwargs)
    return wrapper


@app.post("/api/officer/login")
def officer_login():
    payload = request.get_json(silent=True) or {}
    name = " ".join(str(payload.get("name", "")).split())
    department = str(payload.get("department", ""))
    password = str(payload.get("password", "")).strip()

    if not 2 <= len(name) <= 60:
        raise BadRequest("Please enter your name (2-60 characters).")
    if department not in DEPARTMENTS:
        raise BadRequest("Please choose your department.")
    # PROTOTYPE ONLY: the password is the officer's name. Replace with real accounts
    # (hashed passwords in an officers table) before using this anywhere real.
    if not secrets.compare_digest(password.casefold().encode(), name.casefold().encode()):
        return jsonify(error="Wrong name or password."), 401

    session.clear()
    session["officer"] = {"name": name, "department": department}
    return jsonify(officer=_officer_info(session["officer"]))


@app.post("/api/officer/logout")
def officer_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/officer/me")
@officer_required
def officer_me():
    return jsonify(officer=_officer_info(_officer()))


@app.get("/api/officer/tickets")
@officer_required
def officer_tickets():
    dept = _officer()["department"]
    views = [_officer_view(t, fb, ur) for t, fb, ur in db.department_tickets(dept)]

    def sort_key(v):
        if v["status"] == "Resolved":  # resolved last, newest first
            return (1, 0, -(_ts(v["action_updated_at"]) or 0))
        return (0, db.SEVERITY_RANK.get(v["severity"], 9), _ts(v["deadline"]) or 0)

    views.sort(key=sort_key)
    now = datetime.now()
    open_views = [v for v in views if v["status"] != "Resolved"]
    return jsonify(
        department={"key": dept, **DEPARTMENTS[dept]},
        sla_days=db.SLA_DAYS,
        counts={
            "open": len(open_views),
            "overdue": sum(1 for v in open_views if v["deadline"] and datetime.fromisoformat(v["deadline"]) < now),
            "critical": sum(1 for v in open_views if v["severity"] == "Critical"),
            "resolved": len(views) - len(open_views),
        },
        tickets=views,
    )


@app.post("/api/officer/tickets/<code>/update")
@officer_required
def officer_update_ticket(code):
    officer = _officer()
    row = _get_row_or_404(code)
    if row["category"] != officer["department"] or row["status"] == db.STATUS_DRAFT:
        abort(404)

    payload = request.get_json()
    status = payload.get("status")
    action = str(payload.get("action_taken", "")).strip()
    if status not in db.PUBLIC_STATUSES:
        raise BadRequest("Invalid status.")
    if len(action) > 2000:
        raise BadRequest("Action taken is too long (max 2000 characters).")
    if status == "Resolved" and not action:
        raise BadRequest("Describe the action taken before marking the complaint Resolved.")

    db.set_status_and_action(code, status, action, by=officer["name"])
    row = db.get_ticket_row(code)
    return jsonify(ticket=_officer_view(row, db.ticket_feedback(row), db.ticket_urgency_requests(row)))


@app.post("/api/officer/urgency/<int:request_id>/decide")
@officer_required
def officer_decide_urgency(request_id):
    officer = _officer()
    urgency = db.get_urgency_row(request_id)
    ticket = db.get_ticket_row(urgency["ticket_code"]) if urgency else None
    if not ticket or ticket["category"] != officer["department"]:
        abort(404)
    if urgency["status"] != "Pending review":
        raise BadRequest("This request has already been decided.")

    payload = request.get_json()
    decision = payload.get("decision")
    note = " ".join(str(payload.get("note", "")).split())[:300]
    if decision not in ("Accepted", "Rejected"):
        raise BadRequest("Decision must be Accepted or Rejected.")
    if decision == "Rejected" and not note:
        raise BadRequest("Please give a short reason for rejecting the request.")

    db.decide_urgency_request(request_id, decision, note)
    return jsonify(ok=True)


@app.get("/api/officer/notifications")
@officer_required
def officer_notifications():
    dept = _officer()["department"]
    db.refresh_overdue_reminders(dept)
    rows, unread = db.list_notifications(dept)
    return jsonify(
        unread=unread,
        notifications=[{
            "id": n["id"],
            "kind": n["kind"],
            "emergency": n["kind"] in ("critical", "urgency"),
            "ticket_id": n["ticket_code"],
            "message": n["message"],
            "created_at": n["created_at"],
            "read": n["read_at"] is not None,
        } for n in rows],
    )


@app.post("/api/officer/notifications/read")
@officer_required
def officer_notifications_read():
    payload = request.get_json()
    if payload.get("all"):
        db.mark_notifications_read(_officer()["department"])
    else:
        ids = [int(i) for i in payload.get("ids", []) if str(i).isdigit()][:100]
        db.mark_notifications_read(_officer()["department"], ids)
    return jsonify(ok=True)


def _officer_info(officer):
    return {**officer, "department_name": DEPARTMENTS[officer["department"]]["name"],
            "department_icon": DEPARTMENTS[officer["department"]]["icon"]}


def _ts(iso):
    return datetime.fromisoformat(iso).timestamp() if iso else None


# ---------------------------------------------------------------------------
# Views - what each audience may see
# ---------------------------------------------------------------------------

def _photo_urls(row):
    return [f"/uploads/{p}" for p in db.photo_list(row)]


def _private_view(row):
    """Everything, for the citizen who filed the ticket."""
    return {
        "ticket_id": row["ticket_code"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "confirmed_at": row["confirmed_at"],
        **{f: row[f] for f in db.AI_FIELDS},
        "complaint_text": row["complaint_text"],
        "location_input": row["location_input"],
        "photos": db.photo_list(row),
        "photo_urls": _photo_urls(row),
        "action_taken": row["action_taken"],
        "action_updated_at": row["action_updated_at"],
    }


def _public_view(row, feedback, urgency):
    """For everyone. Shows the AI summary, NOT the citizen's raw text (it may contain
    names or phone numbers)."""
    return {
        "ticket_id": row["ticket_code"],
        "status": row["status"],
        "confirmed_at": row["confirmed_at"],
        "category": row["category"],
        "department": row["department"],
        "severity": row["severity"],
        "severity_reason": row["severity_reason"],
        "summary": row["summary"],
        "detected_language": row["detected_language"],
        "location": row["location"],
        "photo_urls": _photo_urls(row),
        "action_taken": row["action_taken"],
        "action_updated_at": row["action_updated_at"],
        "feedback": feedback,
        "urgency_requests": [_urgency_public(u) for u in urgency],
        "can_request_urgency": row["status"] in db.URGENCY_OPEN_STATUSES,
        "target_date": _iso(db.deadline_for(row, urgency)),
    }


def _officer_view(row, feedback, urgency):
    """Everything an officer of this department needs, incl. urgency reasons + proof."""
    return {
        **_private_view(row),
        "action_by": row["action_by"],
        "deadline": _iso(db.deadline_for(row, urgency)),
        "sla_days": db.SLA_DAYS.get(row["severity"]),
        "feedback": feedback,
        "urgency_requests": [_urgency_private({**dict(u), "ticket_code": row["ticket_code"]}) for u in urgency],
    }


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def _urgency_public(u):
    """Everyone sees that urgency was requested - NOT the reason or proof (they may be
    personal, e.g. a hospital letter)."""
    return {
        "id": u["id"],
        "requested_days": u["requested_days"],
        "status": u["status"],
        "ai_verdict": u["ai_verdict"],
        "from_complainant": bool(u["from_complainant"]),
        "officer_note": u["officer_note"],
        "created_at": u["created_at"],
        "decided_at": u["decided_at"],
    }


def _urgency_private(u):
    """For the citizen who made the request (and later, officers)."""
    return {
        **_urgency_public(u),
        "ticket_id": u["ticket_code"],
        "reason": u["reason"],
        "proof_urls": [f"/uploads/{p}" for p in db.proof_list(u)],
        "ai_note": u["ai_note"],
        "ai_engine": u["ai_engine"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token():
    return request.headers.get("X-Edit-Token", "")


def _get_row_or_404(code):
    row = db.get_ticket_row(code)
    if not row:
        abort(404)
    return row


def _require_owned_draft(code):
    row = _get_row_or_404(code)
    if not db.token_matches(row, _token()):
        abort(403)
    if row["status"] != db.STATUS_DRAFT:
        raise BadRequest("This ticket is already confirmed and can no longer be edited.")
    return row


def _read_text_fields():
    text = (request.form.get("complaint") or "").strip()
    location = (request.form.get("location") or "").strip()[:300]
    if len(text) > MAX_TEXT_CHARS:
        raise BadRequest(f"Complaint is too long (max {MAX_TEXT_CHARS} characters).")
    return text, location


def _uploaded_files(field="photos"):
    return [f for f in request.files.getlist(field) if f and f.filename]


def _save_photos(files):
    """Validate + shrink + save uploads. Returns the list of stored file names."""
    prepared = [_prepare_image(f.read()) for f in files]  # validate all before saving any
    names = []
    for data in prepared:
        name = f"{uuid.uuid4().hex}.jpg"
        with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
            fh.write(data)
        names.append(name)
    return names


def _load_photos(names):
    images = []
    for name in names:
        with open(os.path.join(UPLOAD_DIR, name), "rb") as fh:
            images.append((fh.read(), "image/jpeg"))
    return images


def _delete_photo(name):
    try:
        os.remove(os.path.join(UPLOAD_DIR, os.path.basename(name)))
    except FileNotFoundError:
        pass


def _prepare_image(raw):
    """Check the upload really is an image, shrink it, and re-save as JPEG.

    Re-encoding also strips EXIF metadata (e.g. the phone's GPS) from the stored copy.
    """
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise BadRequest("One of the files is not a valid image. Please upload JPG, PNG or WEBP photos.")

    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise BadRequest("Unsupported image type. Please upload JPG, PNG or WEBP photos.")
    if len(raw) > 10 * 1024 * 1024:
        raise BadRequest("Each photo must be 10 MB or smaller.")

    img = ImageOps.exif_transpose(img)  # keep phone photos the right way up
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


if __name__ == "__main__":
    app.run(debug=True)
