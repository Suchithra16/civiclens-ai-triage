"""
CivicLens - SQLite storage.

Ticket life cycle (status column):
    Draft        -> AI ticket created, waiting for the citizen to Edit / Confirm
                    (never shown in the public department history)
    Confirmed    -> citizen confirmed; visible in the department log, action pending
    In Progress  -> officer is working on it            (set by officer portal)
    Resolved     -> officer marked it done              (set by officer portal)

Each ticket has a secret "edit token" given only to the citizen who filed it.
It is needed to edit/confirm the draft. We store only its SHA-256 hash.

Deadlines (from confirmation): Critical 3 days, High 8, Medium 15, Low 30.
An accepted urgency request can bring the deadline forward.

Officer notifications (per department):
    critical -> a Critical complaint was confirmed           (emergency)
    urgency  -> a citizen requested urgent action             (emergency)
    overdue  -> automatic reminder, repeated every day a complaint stays past its deadline

Urgency requests ("Request urgent action") on confirmed tickets:
    Pending review -> Accepted / Rejected   (decided by an officer)
Each has its own secret token so only the requester (and officers) can see the
reason and proof.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "civiclens.db")
SCHEMA_VERSION = 3

STATUS_DRAFT = "Draft"
PUBLIC_STATUSES = ["Confirmed", "In Progress", "Resolved"]
URGENCY_OPEN_STATUSES = ["Confirmed", "In Progress"]   # tickets that can get urgency requests
URGENCY_STATUSES = ["Pending review", "Accepted", "Rejected"]

SLA_DAYS = {"Critical": 3, "High": 8, "Medium": 15, "Low": 30}
SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

AI_FIELDS = [
    "category", "severity", "severity_reason", "department", "summary",
    "detected_language", "location", "english_translation", "image_observations",
    "ai_engine",
]


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    with _db() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        old_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()
        if old_table and version < SCHEMA_VERSION:
            # Database from an earlier step: keep it as a backup table, start fresh.
            conn.execute(f"ALTER TABLE tickets RENAME TO tickets_backup_v{version}")

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_code         TEXT UNIQUE,
                edit_token_hash     TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'Draft',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                confirmed_at        TEXT,

                -- what the citizen gave us
                complaint_text      TEXT NOT NULL DEFAULT '',
                location_input      TEXT NOT NULL DEFAULT '',
                photos              TEXT NOT NULL DEFAULT '[]',   -- JSON list of file names

                -- what the AI produced
                category            TEXT,
                severity            TEXT,
                severity_reason     TEXT,
                department          TEXT,
                summary             TEXT,
                detected_language   TEXT,
                location            TEXT,
                english_translation TEXT,
                image_observations  TEXT,
                ai_engine           TEXT,

                -- written by the municipal officer
                action_taken        TEXT NOT NULL DEFAULT '',
                action_updated_at   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_category_status
                ON tickets (category, status);

            CREATE TABLE IF NOT EXISTS feedback (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id        INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                text             TEXT NOT NULL,
                from_complainant INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_ticket ON feedback (ticket_id);

            CREATE TABLE IF NOT EXISTS urgency_requests (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id        INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                token_hash       TEXT NOT NULL,
                reason           TEXT NOT NULL,
                requested_days   INTEGER NOT NULL,
                proof            TEXT NOT NULL DEFAULT '[]',   -- JSON list of file names
                from_complainant INTEGER NOT NULL DEFAULT 0,
                ai_verdict       TEXT,          -- Supported / Weak / Not supported / Unverified
                ai_note          TEXT,
                ai_engine        TEXT,
                status           TEXT NOT NULL DEFAULT 'Pending review',
                officer_note     TEXT NOT NULL DEFAULT '',
                created_at       TEXT NOT NULL,
                decided_at       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_urgency_ticket ON urgency_requests (ticket_id);

            CREATE TABLE IF NOT EXISTS notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                department  TEXT NOT NULL,       -- department key, e.g. "roads"
                kind        TEXT NOT NULL,       -- critical | urgency | overdue
                ticket_id   INTEGER REFERENCES tickets(id) ON DELETE CASCADE,
                urgency_id  INTEGER,
                message     TEXT NOT NULL,
                dedupe_key  TEXT UNIQUE,         -- stops the same notification being added twice
                created_at  TEXT NOT NULL,
                read_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_notifications_dept ON notifications (department, created_at);
            """
        )
        # columns added in later steps (safe to run every time)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
        if "action_by" not in cols:
            conn.execute("ALTER TABLE tickets ADD COLUMN action_by TEXT NOT NULL DEFAULT ''")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ---------------------------------------------------------------------------
# Edit tokens
# ---------------------------------------------------------------------------

def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(row, token, column="edit_token_hash"):
    if not row or not token:
        return False
    return hmac.compare_digest(row[column], _hash_token(token))


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def create_ticket(ai, complaint_text, location_input, photos, created_at=None):
    """Create a Draft ticket. Returns (ticket_code, edit_token)."""
    token = secrets.token_urlsafe(24)
    when = created_at or _now()
    with _db() as conn:
        cur = conn.execute(
            f"""INSERT INTO tickets (edit_token_hash, status, created_at, updated_at,
                                     complaint_text, location_input, photos,
                                     {", ".join(AI_FIELDS)})
                VALUES (?, ?, ?, ?, ?, ?, ?, {", ".join("?" * len(AI_FIELDS))})""",
            (_hash_token(token), STATUS_DRAFT, when, when,
             complaint_text, location_input, json.dumps(photos),
             *[ai.get(f, "") for f in AI_FIELDS]),
        )
        ticket_id = cur.lastrowid
        code = f"CL-{when[:10].replace('-', '')}-{ticket_id:05d}"
        conn.execute("UPDATE tickets SET ticket_code = ? WHERE id = ?", (code, ticket_id))
    return code, token


def update_draft(code, ai, complaint_text, location_input, photos):
    with _db() as conn:
        conn.execute(
            f"""UPDATE tickets
                SET complaint_text = ?, location_input = ?, photos = ?, updated_at = ?,
                    {", ".join(f"{f} = ?" for f in AI_FIELDS)}
                WHERE ticket_code = ? AND status = ?""",
            (complaint_text, location_input, json.dumps(photos), _now(),
             *[ai.get(f, "") for f in AI_FIELDS], code, STATUS_DRAFT),
        )


def confirm_ticket(code, at=None):
    when = at or _now()
    with _db() as conn:
        cur = conn.execute(
            "UPDATE tickets SET status = 'Confirmed', confirmed_at = ?, updated_at = ? "
            "WHERE ticket_code = ? AND status = ?",
            (when, when, code, STATUS_DRAFT),
        )
        if cur.rowcount:
            t = conn.execute("SELECT * FROM tickets WHERE ticket_code = ?", (code,)).fetchone()
            if t["severity"] == "Critical":
                _add_notification(conn, t["category"], "critical", t["id"],
                                  f"New CRITICAL complaint {code}: {t['summary']}",
                                  f"critical:{t['id']}", at=when)


def set_status_and_action(code, status, action_taken, at=None, by=""):
    """Officer update: status (Confirmed / In Progress / Resolved) + action taken."""
    if status not in PUBLIC_STATUSES:
        raise ValueError(f"status must be one of {PUBLIC_STATUSES}")
    when = at or _now()
    with _db() as conn:
        conn.execute(
            "UPDATE tickets SET status = ?, action_taken = ?, action_updated_at = ?, updated_at = ?, "
            "action_by = ? WHERE ticket_code = ? AND status != ?",
            (status, action_taken, when, when, by, code, STATUS_DRAFT),
        )
        if status == "Resolved":  # nothing left to remind anyone about
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND ticket_id = "
                "(SELECT id FROM tickets WHERE ticket_code = ?)",
                (when, code),
            )


def get_ticket_row(code):
    with _db() as conn:
        return conn.execute("SELECT * FROM tickets WHERE ticket_code = ?", (code,)).fetchone()


def photo_list(row):
    try:
        return json.loads(row["photos"] or "[]")
    except ValueError:
        return []


# ---------------------------------------------------------------------------
# Department views
# ---------------------------------------------------------------------------

def department_counts():
    """{category: {"Confirmed": n, "In Progress": n, "Resolved": n, "total": n}}"""
    counts = {}
    with _db() as conn:
        rows = conn.execute(
            "SELECT category, status, COUNT(*) AS n FROM tickets "
            "WHERE status != ? GROUP BY category, status",
            (STATUS_DRAFT,),
        ).fetchall()
    for r in rows:
        c = counts.setdefault(r["category"], {s: 0 for s in PUBLIC_STATUSES} | {"total": 0})
        c[r["status"]] = r["n"]
        c["total"] += r["n"]
    return counts


def department_tickets(category):
    """Confirmed / in-progress / resolved tickets of one department, newest first."""
    with _db() as conn:
        tickets = conn.execute(
            "SELECT * FROM tickets WHERE category = ? AND status != ? "
            "ORDER BY confirmed_at DESC, id DESC",
            (category, STATUS_DRAFT),
        ).fetchall()
        ids = [t["id"] for t in tickets] or [-1]
        fb_rows = conn.execute(
            f"SELECT * FROM feedback WHERE ticket_id IN ({','.join('?' * len(ids))}) "
            "ORDER BY created_at, id",
            ids,
        ).fetchall()

        ur_rows = conn.execute(
            f"SELECT * FROM urgency_requests WHERE ticket_id IN ({','.join('?' * len(ids))}) "
            "ORDER BY created_at, id",
            ids,
        ).fetchall()

    feedback, urgency = {}, {}
    for f in fb_rows:
        feedback.setdefault(f["ticket_id"], []).append(_feedback_dict(f))
    for u in ur_rows:
        urgency.setdefault(u["ticket_id"], []).append(u)
    return [(t, feedback.get(t["id"], []), urgency.get(t["id"], [])) for t in tickets]


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def add_feedback(ticket_row, text, from_complainant, at=None):
    when = at or _now()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO feedback (ticket_id, text, from_complainant, created_at) VALUES (?, ?, ?, ?)",
            (ticket_row["id"], text, 1 if from_complainant else 0, when),
        )
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _feedback_dict(row)


def ticket_feedback(ticket_row):
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE ticket_id = ? ORDER BY created_at, id",
            (ticket_row["id"],),
        ).fetchall()
    return [_feedback_dict(f) for f in rows]


def _feedback_dict(f):
    return {
        "id": f["id"],
        "text": f["text"],
        "from_complainant": bool(f["from_complainant"]),
        "created_at": f["created_at"],
    }


# ---------------------------------------------------------------------------
# Urgency requests ("Request urgent action")
# ---------------------------------------------------------------------------

def create_urgency_request(ticket_row, reason, days, proof, check, from_complainant, at=None):
    """Returns (request_id, request_token)."""
    token = secrets.token_urlsafe(24)
    when = at or _now()
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO urgency_requests (ticket_id, token_hash, reason, requested_days, proof,
                                             from_complainant, ai_verdict, ai_note, ai_engine, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticket_row["id"], _hash_token(token), reason, days, json.dumps(proof),
             1 if from_complainant else 0, check["verdict"], check["note"], check["ai_engine"], when),
        )
        request_id = cur.lastrowid
        _add_notification(
            conn, ticket_row["category"], "urgency", ticket_row["id"],
            f"Citizen requested urgent action on {ticket_row['ticket_code']} within {days} "
            f"day{'s' if days != 1 else ''}. AI proof check: {check['verdict']}.",
            f"urgency:{request_id}", urgency_id=request_id, at=when,
        )
    return request_id, token


def get_urgency_row(request_id):
    with _db() as conn:
        return conn.execute(
            """SELECT u.*, t.ticket_code FROM urgency_requests u
               JOIN tickets t ON t.id = u.ticket_id WHERE u.id = ?""",
            (request_id,),
        ).fetchone()


def ticket_urgency_requests(ticket_row):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM urgency_requests WHERE ticket_id = ? ORDER BY created_at, id",
            (ticket_row["id"],),
        ).fetchall()


def decide_urgency_request(request_id, status, officer_note="", at=None):
    """For the officer portal (and the demo seed script)."""
    if status not in URGENCY_STATUSES:
        raise ValueError(f"status must be one of {URGENCY_STATUSES}")
    when = at or _now()
    with _db() as conn:
        conn.execute(
            "UPDATE urgency_requests SET status = ?, officer_note = ?, decided_at = ? WHERE id = ?",
            (status, officer_note, when, request_id),
        )
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE urgency_id = ? AND read_at IS NULL",
            (when, request_id),
        )


def proof_list(row):
    try:
        return json.loads(row["proof"] or "[]")
    except ValueError:
        return []


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

def _parse(iso):
    return datetime.fromisoformat(iso) if iso else None


def deadline_for(ticket, urgency_rows=()):
    """When the complaint should be resolved by (datetime), or None for drafts.

    Severity deadline counted from confirmation; an Accepted urgency request
    can bring it forward to "request date + requested days".
    """
    start = _parse(ticket["confirmed_at"])
    if not start:
        return None
    deadline = start + timedelta(days=SLA_DAYS.get(ticket["severity"], 15))
    for u in urgency_rows:
        if u["status"] == "Accepted":
            deadline = min(deadline, _parse(u["created_at"]) + timedelta(days=u["requested_days"]))
    return deadline


# ---------------------------------------------------------------------------
# Officer notifications
# ---------------------------------------------------------------------------

def _add_notification(conn, department, kind, ticket_id, message, dedupe_key, urgency_id=None, at=None):
    conn.execute(
        """INSERT OR IGNORE INTO notifications
               (department, kind, ticket_id, urgency_id, message, dedupe_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (department, kind, ticket_id, urgency_id, message, dedupe_key, at or _now()),
    )


def refresh_overdue_reminders(department, now=None):
    """Add one reminder per open complaint per day it stays past its deadline."""
    now = now or datetime.now()
    with _db() as conn:
        tickets = conn.execute(
            "SELECT * FROM tickets WHERE category = ? AND status IN ('Confirmed', 'In Progress')",
            (department,),
        ).fetchall()
        for t in tickets:
            urgency = conn.execute(
                "SELECT * FROM urgency_requests WHERE ticket_id = ?", (t["id"],)
            ).fetchall()
            deadline = deadline_for(t, urgency)
            if not deadline or now <= deadline:
                continue
            days_over = (now - deadline).days
            late = "is now overdue" if days_over == 0 else \
                f"is overdue by {days_over} day{'s' if days_over != 1 else ''}"
            _add_notification(
                conn, department, "overdue", t["id"],
                f"{t['ticket_code']} ({t['severity']}) {late}. It should have been resolved by "
                f"{deadline:%d %b %Y}. Current status: {t['status']}.",
                f"overdue:{t['id']}:{days_over}",
                at=(deadline + timedelta(days=days_over)).isoformat(timespec="seconds"),
            )


def list_notifications(department, limit=50):
    with _db() as conn:
        rows = conn.execute(
            """SELECT n.*, t.ticket_code FROM notifications n
               LEFT JOIN tickets t ON t.id = n.ticket_id
               WHERE n.department = ? ORDER BY n.created_at DESC, n.id DESC LIMIT ?""",
            (department, limit),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE department = ? AND read_at IS NULL",
            (department,),
        ).fetchone()[0]
    return rows, unread


def mark_notifications_read(department, ids=None):
    """Mark the given notification ids (or all, if ids is None) as read."""
    with _db() as conn:
        if ids is None:
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE department = ? AND read_at IS NULL",
                (_now(), department),
            )
        elif ids:
            conn.execute(
                f"UPDATE notifications SET read_at = ? WHERE department = ? AND read_at IS NULL "
                f"AND id IN ({','.join('?' * len(ids))})",
                (_now(), department, *ids),
            )
