"""
EIA Voice Platform — Flask + PostgreSQL + Cloudinary
Production-ready version for Render deployment.
"""

import hashlib
import secrets
import os
import json
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import psycopg2
import psycopg2.extras
import cloudinary
import cloudinary.uploader

from flask import (
    Flask, render_template, redirect, url_for,
    request, session, jsonify, flash, abort, g
)
from werkzeug.utils import secure_filename

# ─── Config ───────────────────────────────────────────────────────────────────
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "mp4", "mov", "webm", "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt"}
MAX_UPLOAD_BYTES   = 30 * 1024 * 1024  # 30 MB

# Cloudinary configuration (set via env vars on Render)
cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    api_key    = os.environ.get("CLOUDINARY_API_KEY", ""),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", ""),
    secure     = True
)

ROLES = ["student", "teacher", "senator", "admin", "super_admin"]

# ─── Channel definitions ──────────────────────────────────────────────────────
# all_school      → everyone sees it
# announcement    → posted by senators/teachers/admins, seen by everyone (students can comment)
# senate          → students POST concerns here; ONLY senators see it — no other student ever sees it
# senate_disc     → senators discussing among themselves ONLY; students cannot see
# teachers_admins → senators/teachers can post here; teachers AND admins see it; senators CANNOT see replies
# teachers        → teachers talking among themselves; admins see it; senators/students cannot
# admins          → admins only; teachers CANNOT see; senators CANNOT see
# super_admin     → super admin only

# What each role is ALLOWED to post to
ROLE_RECIPIENTS = {
    "student":     ["all_school", "senate"],
    "senator":     ["all_school", "senate_disc", "teachers_admins", "teachers", "admins", "announcement"],
    "teacher":     ["all_school", "teachers", "teachers_admins", "announcement"],
    "admin":       ["all_school", "teachers", "teachers_admins", "admins", "announcement", "super_admin"],
    "super_admin": ["all_school", "senate", "senate_disc", "teachers", "teachers_admins", "admins", "announcement", "super_admin"],
}

# What each role can SEE in their feed
ROLE_VISIBLE = {
    # Students: ONLY whole-school + announcements. Cannot see senate at all (not even their own posts there).
    "student":     ["all_school", "announcement"],
    # Senators: + student petitions to senate + their own senate discussions + teachers_admins channel
    #           They CANNOT see teachers-only or admins-only discussions
    "senator":     ["all_school", "announcement", "senate", "senate_disc", "teachers_admins"],
    # Teachers: + teachers channel + teachers_admins channel. CANNOT see admins-only.
    "teacher":     ["all_school", "announcement", "teachers", "teachers_admins"],
    # Admins: + teachers + teachers_admins + admins-only. Cannot see super_admin channel.
    "admin":       ["all_school", "announcement", "teachers", "teachers_admins", "admins"],
    # Super admin sees everything
    "super_admin": None,
}

RECIPIENT_LABELS = {
    "all_school":      "Whole School",
    "senate":          "→ Senate (petition/concern)",
    "senate_disc":     "Senate Discussion",
    "teachers_admins": "Teachers & Admins",
    "announcement":    "📢 Announcement (all students)",
    "teachers":        "Teachers Only",
    "admins":          "Admins Only",
    "super_admin":     "Super Admin",
}

# ─── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key        = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Render gives postgres:// but psycopg2 needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ─── DB helpers (PostgreSQL) ──────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g.db.autocommit = False
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()

def _pg(sql):
    """Convert SQLite ? placeholders to PostgreSQL %s placeholders."""
    return sql.replace("?", "%s")

def query(sql, args=(), one=False):
    db  = get_db()
    cur = db.cursor()
    cur.execute(_pg(sql), args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def execute(sql, args=()):
    db  = get_db()
    cur = db.cursor()
    cur.execute(_pg(sql), args)
    db.commit()
    return cur

def init_db():
    """Create all tables (PostgreSQL) and seed super-admin account."""
    db  = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id          SERIAL PRIMARY KEY,
        username    TEXT    UNIQUE NOT NULL,
        password    TEXT    NOT NULL,
        name        TEXT    NOT NULL,
        role        TEXT    NOT NULL DEFAULT 'student',
        bio         TEXT    DEFAULT '',
        avatar      TEXT    DEFAULT '',
        anon_name   TEXT,
        year_group  TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id          TEXT    PRIMARY KEY,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL,
        media_path  TEXT    DEFAULT '',
        media_type  TEXT    DEFAULT '',
        media_name  TEXT    DEFAULT '',
        is_anon     INTEGER NOT NULL DEFAULT 0,
        recipient   TEXT    NOT NULL DEFAULT 'all_school',
        flagged     INTEGER NOT NULL DEFAULT 0,
        flag_reason TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id          TEXT    PRIMARY KEY,
        post_id     TEXT    NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL,
        is_anon     INTEGER NOT NULL DEFAULT 0,
        flagged     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reactions (
        id          SERIAL PRIMARY KEY,
        post_id     TEXT    REFERENCES posts(id)    ON DELETE CASCADE,
        comment_id  TEXT    REFERENCES comments(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji       TEXT    NOT NULL,
        UNIQUE (post_id, user_id, emoji),
        UNIQUE (comment_id, user_id, emoji)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS follows (
        follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL,
        PRIMARY KEY (follower_id, followee_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id          TEXT    PRIMARY KEY,
        user_a      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        user_b      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id              TEXT    PRIMARY KEY,
        conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        sender_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content         TEXT    NOT NULL,
        is_anon         INTEGER NOT NULL DEFAULT 0,
        is_read         INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id          SERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text        TEXT    NOT NULL,
        link        TEXT    DEFAULT '',
        notif_type  TEXT    DEFAULT 'info',
        is_read     INTEGER NOT NULL DEFAULT 0,
        actor_id    INTEGER,
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS classes (
        id          SERIAL PRIMARY KEY,
        name        TEXT    NOT NULL,
        subject     TEXT    NOT NULL DEFAULT '',
        year_group  TEXT    NOT NULL,
        teacher_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS class_members (
        class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at   TEXT    NOT NULL,
        PRIMARY KEY (class_id, student_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS class_posts (
        id          TEXT    PRIMARY KEY,
        class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT    NOT NULL DEFAULT '',
        content     TEXT    NOT NULL DEFAULT '',
        post_type   TEXT    NOT NULL DEFAULT 'note',
        file_path   TEXT    DEFAULT '',
        file_name   TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS class_replies (
        id          TEXT    PRIMARY KEY,
        post_id     TEXT    NOT NULL REFERENCES class_posts(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL DEFAULT '',
        file_path   TEXT    DEFAULT '',
        file_name   TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id          SERIAL PRIMARY KEY,
        name        TEXT    NOT NULL UNIQUE,
        description TEXT    DEFAULT '',
        creator_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS channel_follows (
        channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at   TEXT    NOT NULL,
        PRIMARY KEY (channel_id, user_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS channel_posts (
        id          TEXT    PRIMARY KEY,
        channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL,
        media_path  TEXT    DEFAULT '',
        media_type  TEXT    DEFAULT '',
        media_name  TEXT    DEFAULT '',
        is_anon     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS channel_comments (
        id          TEXT    PRIMARY KEY,
        post_id     TEXT    NOT NULL REFERENCES channel_posts(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL,
        is_anon     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    )""")

    # Seed super admin
    cur.execute("SELECT id FROM users WHERE username='superadmin'")
    if not cur.fetchone():
        pw   = hashlib.sha256("SuperAdmin@EIA2024!".encode()).hexdigest()
        anon = f"Shadow_{secrets.token_hex(3).upper()}"
        cur.execute(
            "INSERT INTO users (username,password,name,role,anon_name,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            ("superadmin", pw, "Super Administrator", "super_admin", anon, _now())
        )

    # ── Runtime migrations: safely add columns that may not exist yet ──────────
    # ── Assignments ──────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS assignments (
        id          TEXT PRIMARY KEY,
        class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        teacher_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        description TEXT DEFAULT '',
        due_date    TEXT NOT NULL,
        max_marks   INTEGER DEFAULT 100,
        created_at  TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id            TEXT PRIMARY KEY,
        assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
        student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content       TEXT DEFAULT '',
        file_path     TEXT DEFAULT '',
        file_name     TEXT DEFAULT '',
        grade         TEXT DEFAULT '',
        feedback      TEXT DEFAULT '',
        submitted_at  TEXT NOT NULL,
        graded_at     TEXT DEFAULT '',
        UNIQUE(assignment_id, student_id)
    )""")

    # ── Quizzes ───────────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quizzes (
        id          TEXT PRIMARY KEY,
        class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        teacher_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        is_open     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_questions (
        id          TEXT PRIMARY KEY,
        quiz_id     TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        question    TEXT NOT NULL,
        option_a    TEXT NOT NULL,
        option_b    TEXT NOT NULL,
        option_c    TEXT NOT NULL,
        option_d    TEXT NOT NULL,
        correct     TEXT NOT NULL,
        position    INTEGER DEFAULT 0
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quiz_attempts (
        id          TEXT PRIMARY KEY,
        quiz_id     TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
        student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        score       INTEGER DEFAULT 0,
        total       INTEGER DEFAULT 0,
        answers     TEXT DEFAULT '',
        submitted_at TEXT NOT NULL,
        UNIQUE(quiz_id, student_id)
    )""")

    # ── Attendance ────────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance_sessions (
        id          TEXT PRIMARY KEY,
        class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        teacher_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        label       TEXT DEFAULT '',
        open_until  TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance_records (
        id            TEXT PRIMARY KEY,
        session_id    TEXT NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
        student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        marked_at     TEXT NOT NULL,
        UNIQUE(session_id, student_id)
    )""")

    # ── Resource Library ──────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS resources (
        id          TEXT PRIMARY KEY,
        class_id    INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
        teacher_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_path   TEXT NOT NULL,
        file_name   TEXT NOT NULL,
        subject_tag TEXT DEFAULT '',
        created_at  TEXT NOT NULL
    )""")

    # ── Polls ─────────────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS polls (
        id          TEXT PRIMARY KEY,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        class_id    INTEGER,
        question    TEXT NOT NULL,
        opt_a       TEXT NOT NULL,
        opt_b       TEXT NOT NULL,
        opt_c       TEXT DEFAULT '',
        opt_d       TEXT DEFAULT '',
        scope       TEXT DEFAULT 'feed',
        ends_at     TEXT DEFAULT '',
        created_at  TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS poll_votes (
        id          TEXT PRIMARY KEY,
        poll_id     TEXT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        choice      TEXT NOT NULL,
        UNIQUE(poll_id, user_id)
    )""")

    # ── Events ────────────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id          TEXT PRIMARY KEY,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title       TEXT NOT NULL,
        description TEXT DEFAULT '',
        event_date  TEXT NOT NULL,
        event_time  TEXT DEFAULT '',
        location    TEXT DEFAULT '',
        created_at  TEXT NOT NULL
    )""")

    # ── Peer Tutoring ─────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tutoring_posts (
        id          TEXT PRIMARY KEY,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        post_type   TEXT NOT NULL DEFAULT 'need',
        subject     TEXT NOT NULL,
        description TEXT DEFAULT '',
        is_resolved INTEGER DEFAULT 0,
        created_at  TEXT NOT NULL
    )""")

    # ── Study Groups ─────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS study_groups (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        creator_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        invite_code TEXT UNIQUE NOT NULL,
        created_at  TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS study_group_members (
        group_id    TEXT NOT NULL REFERENCES study_groups(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at   TEXT NOT NULL,
        PRIMARY KEY(group_id, user_id)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS study_group_messages (
        id          TEXT PRIMARY KEY,
        group_id    TEXT NOT NULL REFERENCES study_groups(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""")

    # ── Shoutouts ─────────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shoutouts (
        id          TEXT PRIMARY KEY,
        content     TEXT NOT NULL,
        is_approved INTEGER DEFAULT 0,
        created_at  TEXT NOT NULL
    )""")

    # ── Yearbook ──────────────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS yearbook_years (
        id          SERIAL PRIMARY KEY,
        year_group  TEXT NOT NULL UNIQUE,
        title       TEXT NOT NULL DEFAULT '',
        subtitle    TEXT NOT NULL DEFAULT '',
        class_teacher TEXT NOT NULL DEFAULT '',
        message     TEXT NOT NULL DEFAULT '',
        achievements TEXT NOT NULL DEFAULT '',
        cover_color TEXT NOT NULL DEFAULT '#1a2e5a',
        updated_at  TEXT NOT NULL,
        updated_by  INTEGER REFERENCES users(id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS yearbook_entries (
        id          SERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        year_group  TEXT NOT NULL,
        quote       TEXT NOT NULL DEFAULT '',
        ambition    TEXT NOT NULL DEFAULT '',
        memory      TEXT NOT NULL DEFAULT '',
        nickname    TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL
    )""")

    migrations = [
        "ALTER TABLE posts          ADD COLUMN IF NOT EXISTS media_name  TEXT DEFAULT ''",
        "ALTER TABLE messages       ADD COLUMN IF NOT EXISTS voice_url   TEXT DEFAULT ''",
        "ALTER TABLE messages       ADD COLUMN IF NOT EXISTS msg_type    TEXT DEFAULT 'text'",
        "ALTER TABLE messages       ADD COLUMN IF NOT EXISTS reply_to    TEXT DEFAULT ''",
        "ALTER TABLE messages       ADD COLUMN IF NOT EXISTS reply_preview TEXT DEFAULT ''",
        "ALTER TABLE channel_posts  ADD COLUMN IF NOT EXISTS media_name  TEXT DEFAULT ''",
        "ALTER TABLE class_posts    ADD COLUMN IF NOT EXISTS file_path   TEXT DEFAULT ''",
        "ALTER TABLE class_posts    ADD COLUMN IF NOT EXISTS file_name   TEXT DEFAULT ''",
        "ALTER TABLE class_replies  ADD COLUMN IF NOT EXISTS file_path   TEXT DEFAULT ''",
        "ALTER TABLE class_replies  ADD COLUMN IF NOT EXISTS file_name   TEXT DEFAULT ''",
        "ALTER TABLE users          ADD COLUMN IF NOT EXISTS anon_name   TEXT DEFAULT ''",
        "ALTER TABLE users          ADD COLUMN IF NOT EXISTS year_group  TEXT DEFAULT ''",
        "ALTER TABLE users          ADD COLUMN IF NOT EXISTS bio         TEXT DEFAULT ''",
        "ALTER TABLE users          ADD COLUMN IF NOT EXISTS avatar      TEXT DEFAULT ''",
        "ALTER TABLE notifications  ADD COLUMN IF NOT EXISTS actor_id    INTEGER",
        "ALTER TABLE notifications  ADD COLUMN IF NOT EXISTS notif_type  TEXT DEFAULT 'info'",
    ]
    # Each migration needs its own transaction in PostgreSQL
    db.commit()  # commit table creations first
    for sql in migrations:
        try:
            cur.execute(sql)
            db.commit()
        except Exception as me:
            db.rollback()
            print(f"[migration] skipped: {sql[:60]} ({me})")

    cur.close()
    db.close()


# ─── Utilities ────────────────────────────────────────────────────────────────
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _uid() -> str:
    return secrets.token_hex(10)

def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _is_video(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"mp4","mov","webm"}

def _is_document(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"pdf","doc","docx","ppt","pptx","xls","xlsx","txt"}

def _fmt_time(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z",""))
        return dt.strftime("%b %d, %Y · %I:%M %p")
    except Exception:
        return ts

def _relative_time(ts: str) -> str:
    try:
        dt  = datetime.fromisoformat(ts.replace("Z","")).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        if diff < 60:    return "just now"
        if diff < 3600:  return f"{int(diff//60)}m ago"
        if diff < 86400: return f"{int(diff//3600)}h ago"
        if diff < 604800:return f"{int(diff//86400)}d ago"
        return dt.strftime("%b %d")
    except Exception:
        return ts

def get_anon_name(user_id: int) -> str:
    row = query("SELECT anon_name FROM users WHERE id=?", (user_id,), one=True)
    if row and row["anon_name"]:
        return row["anon_name"]
    name = f"Ghost_{secrets.token_hex(3).upper()}"
    execute("UPDATE users SET anon_name=? WHERE id=?", (name, user_id))
    return name

def save_upload(file, force_raw=False) -> tuple[str, str, str]:
    """Upload file to Cloudinary, return (public_url, media_type, original_name)."""
    if not file or not file.filename:
        return "", "", ""
    if not _allowed(file.filename):
        return "", "", ""
    import re as _re
    original_name = file.filename
    ext = original_name.rsplit(".", 1)[1].lower() if "." in original_name else ""
    if force_raw:
        mtype = "voice"
    elif ext in {"mp4","mov"}:
        mtype = "video"
    elif ext in {"pdf","doc","docx","ppt","pptx","xls","xlsx","txt"}:
        mtype = "document"
    elif ext in {"ogg","m4a","wav","webm"}:
        # webm from browser microphone = audio, treat as raw
        mtype = "voice"
    else:
        mtype = "image"
    try:
        resource_type = "video" if mtype == "video" else ("raw" if mtype in ("document","voice") else "image")
        # Use original filename (sanitised) as public_id so URL keeps the extension
        safe_stem = _re.sub(r'[^a-zA-Z0-9._-]', '_', original_name.rsplit(".", 1)[0])[:60]
        public_id = f"{_uid()}_{safe_stem}"
        if mtype in ("document", "voice") and ext:
            # For raw uploads Cloudinary DOES append extension to URL when public_id has no ext
            # We must include the extension in the public_id so audio can play
            public_id = f"{public_id}.{ext}"
        result = cloudinary.uploader.upload(
            file,
            folder        = "eia_voice",
            resource_type = resource_type,
            public_id     = public_id,
            use_filename  = False,
            overwrite     = False,
            access_mode   = "public",
        )
        url = result.get("secure_url", "")
        return url, mtype, original_name
    except Exception as e:
        app.logger.error(f"Cloudinary upload failed: {e}")
        return "", "", ""

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapped

def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return query("SELECT * FROM users WHERE id=?", (uid,), one=True)

# ─── Notification helpers ─────────────────────────────────────────────────────
def push_notif(user_id: int, text: str, link: str = "", notif_type: str = "info", actor_id=None):
    execute(
        "INSERT INTO notifications (user_id,text,link,notif_type,actor_id,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, text, link, notif_type, actor_id, _now())
    )

def unread_notif_count(user_id: int) -> int:
    r = query("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0", (user_id,), one=True)
    return r["c"] if r else 0

def unread_msg_count(user_id: int) -> int:
    r = query(
        """SELECT COUNT(*) as c FROM messages m
           JOIN conversations c ON m.conversation_id=c.id
           WHERE (c.user_a=? OR c.user_b=?) AND m.sender_id!=? AND m.is_read=0""",
        (user_id, user_id, user_id), one=True
    )
    return r["c"] if r else 0

# ─── Context processor ────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    user = current_user()
    nc   = unread_notif_count(user["id"]) if user else 0
    mc   = unread_msg_count(user["id"])   if user else 0
    return dict(
        cu=user, notif_count=nc, msg_count=mc,
        _fmt=_fmt_time, _rel=_relative_time, _now=_now,
        ROLE_LABELS={"student":"Student","teacher":"Teacher","senator":"Senator",
                     "admin":"Admin","super_admin":"Super Admin"},
        RECIPIENT_LABELS=RECIPIENT_LABELS,
    )

# ─── Post serialiser ─────────────────────────────────────────────────────────
def serialise_post(row, viewer_id=None):
    author = query("SELECT * FROM users WHERE id=?", (row["author_id"],), one=True)
    is_anon = bool(row["is_anon"])
    if is_anon:
        display = get_anon_name(row["author_id"])
        avatar  = ""
        role_shown = "anonymous"
    else:
        display    = author["username"] if author else "unknown"
        avatar     = author["avatar"]   if author else ""
        role_shown = author["role"]     if author else ""

    # Reactions
    raw_reacts = query(
        "SELECT emoji, COUNT(*) as c, STRING_AGG(user_id::text, ',') as uids FROM reactions WHERE post_id=? GROUP BY emoji",
        (row["id"],)
    )
    reactions = {}
    for r in raw_reacts:
        uids = [int(x) for x in (r["uids"] or "").split(",") if x]
        reactions[r["emoji"]] = {"count": r["c"], "liked": (viewer_id in uids) if viewer_id else False}

    # Comments
    raw_cmts = query(
        """SELECT c.*, u.username, u.role, u.anon_name, u.avatar, u.username
           FROM comments c JOIN users u ON c.author_id=u.id
           WHERE c.post_id=? ORDER BY c.created_at ASC""",
        (row["id"],)
    )
    comments = []
    for c in raw_cmts:
        ca_display = c["anon_name"] if (c["is_anon"] and c["anon_name"]) else c["username"]
        ca_avatar  = "" if c["is_anon"] else c["avatar"]
        comments.append({
            "id": c["id"], "content": c["content"],
            "display": ca_display, "avatar": ca_avatar,
            "role": c["role"], "username": c["username"],
            "is_anon": bool(c["is_anon"]),
            "created_at": c["created_at"],
            "author_id": c["author_id"],
            "relative": _relative_time(c["created_at"]),
            "flagged": bool(c["flagged"]) if "flagged" in c.keys() else False,
        })

    # Is following (for reels)
    is_following = False
    if viewer_id and row["author_id"] != viewer_id:
        f = query("SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?", (viewer_id, row["author_id"]), one=True)
        is_following = bool(f)

    heart_data  = reactions.get("❤️", {"count": 0, "liked": False})
    dislike_data = reactions.get("👎", {"count": 0, "liked": False})

    return {
        "id": row["id"], "content": row["content"],
        "media": row["media_path"], "media_path": row["media_path"], "media_type": row["media_type"], "media_name": (row["media_name"] if "media_name" in row.keys() else ""),
        "is_anon": is_anon, "display": display, "display_name": display, "avatar": avatar,
        "role": role_shown, "role_shown": role_shown, "author_id": row["author_id"],
        "recipient": row["recipient"], "flagged": bool(row["flagged"]),
        "flag_reason": row["flag_reason"], "reactions": reactions,
        "comments": comments, "comment_count": len(comments),
        "created_at": row["created_at"], "relative": _relative_time(row["created_at"]),
        "is_following": is_following,
        "user_liked": heart_data["liked"],
        "like_count": heart_data["count"],
        "user_disliked": dislike_data["liked"],
    }

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Auth ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("feed") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("feed"))
    if request.method == "POST":
        uname = request.form.get("username","").strip().lower()
        pw    = request.form.get("password","")
        user  = query("SELECT * FROM users WHERE LOWER(username)=?", (uname,), one=True)
        if user and user["password"] == _hash(pw):
            session.permanent = True
            session.update({"user_id": user["id"], "role": user["role"],
                            "username": user["username"], "name": user["name"]})
            return redirect(request.args.get("next") or url_for("feed"))
        flash("Invalid username or password", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── Feed ─────────────────────────────────────────────────────────────────────
@app.route("/feed")
@login_required
def feed():
    user = current_user()
    role = user["role"]

    visible = ROLE_VISIBLE.get(role, ["all_school", "announcement"])

    if visible is None:  # super_admin sees all
        rows = query("SELECT *, COALESCE(media_name,'') as media_name FROM posts ORDER BY created_at DESC LIMIT 100")
    else:
        placeholders = ",".join("?" * len(visible))
        rows = query(
            f"SELECT *, COALESCE(media_name,'') as media_name FROM posts WHERE recipient IN ({placeholders}) ORDER BY created_at DESC LIMIT 100",
            visible
        )

    posts = [serialise_post(r, user["id"]) for r in rows]
    recipients = ROLE_RECIPIENTS.get(role, ["all_school"])
    return render_template("feed.html", posts=posts, recipients=recipients)

# ─── Create Post ──────────────────────────────────────────────────────────────
@app.route("/post/create", methods=["POST"])
@login_required
def create_post():
    user    = current_user()
    content = request.form.get("content","").strip()
    is_anon   = bool(request.form.get("is_anon"))
    recipient = request.form.get("recipient", "all_school")
    role      = user["role"]

    # Validate recipient is allowed for this role (hard server-side check)
    allowed_recipients = ROLE_RECIPIENTS.get(role, ["all_school"])
    if recipient not in allowed_recipients:
        recipient = allowed_recipients[0]

    # Announcements are never anonymous — author must be identifiable
    if recipient == "announcement":
        is_anon = False

    # Senate petitions from students are never anonymous — senators need to know who sent it
    if recipient == "senate" and role == "student":
        is_anon = False

    media_path, media_type, media_name = "", "", ""
    if "media" in request.files:
        f = request.files["media"]
        if f and f.filename:
            media_path, media_type, media_name = save_upload(f)

    # Must have content OR an uploaded file
    if not content and not media_path:
        flash("Post cannot be empty.", "error")
        return redirect(url_for("feed"))

    pid = _uid()
    execute(
        "INSERT INTO posts (id,author_id,content,media_path,media_type,media_name,is_anon,recipient,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, user["id"], content, media_path, media_type, media_name, 1 if is_anon else 0, recipient, _now())
    )

    # Notify followers
    display = get_anon_name(user["id"]) if is_anon else user["username"]
    followers = query("SELECT follower_id FROM follows WHERE followee_id=?", (user["id"],))
    for f_row in followers:
        push_notif(
            f_row["follower_id"],
            f"{display} made a new post",
            url_for("feed") + f"#post-{pid}",
            "post", user["id"] if not is_anon else None
        )

    return redirect(url_for("feed") + f"#post-{pid}")

# ─── Delete Post ──────────────────────────────────────────────────────────────
@app.route("/post/<pid>/delete", methods=["POST"])
@login_required
def delete_post(pid):
    user = current_user()
    post = query("SELECT * FROM posts WHERE id=?", (pid,), one=True)
    if not post:
        abort(404)
    if post["author_id"] != user["id"] and user["role"] != "super_admin":
        abort(403)
    execute("DELETE FROM posts WHERE id=?", (pid,))
    flash("Post deleted.", "success")
    return redirect(url_for("feed"))

# ─── Flag Post ────────────────────────────────────────────────────────────────
@app.route("/post/<pid>/flag", methods=["POST"])
@login_required
@roles_required("admin","super_admin")
def flag_post(pid):
    reason = request.form.get("reason","Violation of community guidelines")
    execute("UPDATE posts SET flagged=1, flag_reason=? WHERE id=?", (reason, pid))
    flash("Post flagged for review.", "warning")
    return redirect(request.referrer or url_for("feed"))

@app.route("/post/<pid>/unflag", methods=["POST"])
@login_required
@roles_required("admin","super_admin")
def unflag_post(pid):
    execute("UPDATE posts SET flagged=0, flag_reason='' WHERE id=?", (pid,))
    flash("Post unflagged.", "success")
    return redirect(request.referrer or url_for("feed"))

# ─── Reveal Identity (Super Admin only) ───────────────────────────────────────
@app.route("/post/<pid>/reveal", methods=["POST"])
@login_required
@roles_required("super_admin")
def reveal_post_identity(pid):
    post   = query("SELECT * FROM posts WHERE id=?", (pid,), one=True)
    if not post:
        return jsonify({"error": "Not found"}), 404
    author = query("SELECT * FROM users WHERE id=?", (post["author_id"],), one=True)
    if not author:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "name":     author["name"],
        "username": author["username"],
        "role":     author["role"],
        "is_anon":  bool(post["is_anon"]),
        "real_anon_name": author["anon_name"],
    })

# ─── React ────────────────────────────────────────────────────────────────────
@app.route("/post/<pid>/react", methods=["POST"])
@login_required
def react_post(pid):
    user  = current_user()
    data  = request.get_json() or {}
    emoji = data.get("emoji","❤️")

    existing = query(
        "SELECT id FROM reactions WHERE post_id=? AND user_id=? AND emoji=?",
        (pid, user["id"], emoji), one=True
    )
    if existing:
        execute("DELETE FROM reactions WHERE post_id=? AND user_id=? AND emoji=?",
                (pid, user["id"], emoji))
        liked = False
    else:
        try:
            execute("INSERT INTO reactions (post_id,user_id,emoji) VALUES (?,?,?)",
                    (pid, user["id"], emoji))
        except Exception:
            pass
        liked = True
        # Notify post author
        post = query("SELECT * FROM posts WHERE id=?", (pid,), one=True)
        if post and post["author_id"] != user["id"]:
            display = get_anon_name(user["id"])  # always anon in notification
            push_notif(
                post["author_id"],
                f"{display} reacted {emoji} to your post",
                url_for("feed") + f"#post-{pid}", "reaction"
            )

    count = query(
        "SELECT COUNT(*) as c FROM reactions WHERE post_id=? AND emoji=?",
        (pid, emoji), one=True
    )["c"]
    return jsonify({"liked": liked, "count": count})

# ─── Comment ──────────────────────────────────────────────────────────────────
@app.route("/post/<pid>/comment", methods=["POST"])
@login_required
def add_comment(pid):
    user    = current_user()
    content = request.form.get("content","").strip()
    is_anon = bool(request.form.get("is_anon"))
    if not content:
        return redirect(url_for("feed") + f"#post-{pid}")

    cid = _uid()
    execute(
        "INSERT INTO comments (id,post_id,author_id,content,is_anon,created_at) VALUES (?,?,?,?,?,?)",
        (cid, pid, user["id"], content, 1 if is_anon else 0, _now())
    )

    # Notify post author
    post = query("SELECT * FROM posts WHERE id=?", (pid,), one=True)
    if post and post["author_id"] != user["id"]:
        display = get_anon_name(user["id"]) if is_anon else user["username"]
        push_notif(
            post["author_id"],
            f"{display} commented on your post",
            url_for("feed") + f"#post-{pid}", "comment"
        )

    return redirect(url_for("feed") + f"#post-{pid}")

@app.route("/comment/<cid>/delete", methods=["POST"])
@login_required
def delete_comment(cid):
    user    = current_user()
    comment = query("SELECT * FROM comments WHERE id=?", (cid,), one=True)
    if not comment:
        abort(404)
    if comment["author_id"] != user["id"] and user["role"] != "super_admin":
        abort(403)
    pid = comment["post_id"]
    execute("DELETE FROM comments WHERE id=?", (cid,))
    return redirect(url_for("feed") + f"#post-{pid}")

@app.route("/comment/<cid>/flag", methods=["POST"])
@login_required
@roles_required("super_admin")
def flag_comment(cid):
    execute("UPDATE comments SET flagged=1 WHERE id=?", (cid,))
    return jsonify({"ok": True})

@app.route("/comment/<cid>/unflag", methods=["POST"])
@login_required
@roles_required("super_admin")
def unflag_comment(cid):
    execute("UPDATE comments SET flagged=0 WHERE id=?", (cid,))
    return jsonify({"ok": True})

@app.route("/post/<pid>/comments")
@login_required
def get_post_comments(pid):
    """JSON endpoint for fetching comments (used by Reels drawer)."""
    rows = query(
        "SELECT c.*, u.username, u.username, u.avatar, u.anon_name FROM comments c "
        "JOIN users u ON c.author_id=u.id WHERE c.post_id=? ORDER BY c.created_at ASC",
        (pid,)
    )
    comments = []
    for r in rows:
        is_anon = bool(r["is_anon"])
        author = get_anon_name(r["author_id"]) if is_anon else r["username"]
        avatar_url = url_for("serve_media", filename=r["avatar"]) if r["avatar"] and not is_anon else ""
        comments.append({
            "id":       r["id"],
            "author":   author,
            "avatar":   avatar_url,
            "content":  r["content"],
            "time_ago": _relative_time(r["created_at"]),
        })
    return jsonify({"comments": comments})

@app.route("/comment/<cid>/reveal", methods=["POST"])
@login_required
@roles_required("super_admin")
def reveal_comment_identity(cid):
    c = query("SELECT c.*, u.username, u.username, u.role, u.anon_name FROM comments c JOIN users u ON c.author_id=u.id WHERE c.id=?", (cid,), one=True)
    if not c:
        return jsonify({"error": "Comment not found"}), 404
    return jsonify({
        "name": c["name"], "username": c["username"],
        "role": c["role"], "is_anon": bool(c["is_anon"]),
        "anon_name": c["anon_name"], "content": c["content"],
    })






# ─── Profile ──────────────────────────────────────────────────────────────────
@app.route("/profile/<username>")
@login_required
def profile(username):
    viewer = current_user()
    prof   = query("SELECT * FROM users WHERE LOWER(username)=?", (username.lower(),), one=True)
    if not prof:
        abort(404)

    is_self = prof["id"] == viewer["id"]
    is_super = viewer["role"] == "super_admin"

    # Counts
    followers = query("SELECT COUNT(*) as c FROM follows WHERE followee_id=?", (prof["id"],), one=True)["c"]
    following = query("SELECT COUNT(*) as c FROM follows WHERE follower_id=?", (prof["id"],), one=True)["c"]
    i_follow  = bool(query("SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?",
                           (viewer["id"], prof["id"]), one=True))

    # Posts visible to viewer
    # Only show posts the viewer is actually allowed to see
    viewer_visible = ROLE_VISIBLE.get(viewer["role"])
    if viewer_visible is None:  # super_admin sees all
        post_rows = query("SELECT * FROM posts WHERE author_id=? ORDER BY created_at DESC", (prof["id"],))
    else:
        placeholders = ",".join("?" * len(viewer_visible))
        post_rows = query(
            f"SELECT * FROM posts WHERE author_id=? AND recipient IN ({placeholders}) ORDER BY created_at DESC",
            (prof["id"], *viewer_visible)
        )
    posts = [serialise_post(r, viewer["id"]) for r in post_rows]
    post_count = len(posts)

    return render_template("profile.html",
        prof=prof, posts=posts, is_self=is_self, is_super=is_super,
        followers=followers, following=following, i_follow=i_follow,
        post_count=post_count
    )

@app.route("/profile/edit", methods=["GET","POST"])
@login_required
def edit_profile():
    user = current_user()
    if request.method == "POST":
        new_username = request.form.get("username","").strip().lower()
        bio      = request.form.get("bio","").strip()
        avatar   = user["avatar"]
        anon_nm  = request.form.get("anon_name","").strip()

        if "avatar" in request.files:
            f = request.files["avatar"]
            if f and f.filename:
                fname, _, _x = save_upload(f)
                if fname:
                    avatar = fname

        if anon_nm:
            anon_nm = anon_nm[:20]
        else:
            anon_nm = user["anon_name"] or get_anon_name(user["id"])

        # Handle username change
        if new_username and new_username != user["username"]:
            taken = query("SELECT id FROM users WHERE LOWER(username)=? AND id!=?",
                          (new_username, user["id"]), one=True)
            if taken:
                flash("That username is already taken. Choose another.", "error")
                anon_name = user["anon_name"] or get_anon_name(user["id"])
                return render_template("edit_profile.html", user=user, anon_name=anon_name)
            execute("UPDATE users SET username=?,bio=?,avatar=?,anon_name=? WHERE id=?",
                    (new_username, bio, avatar, anon_nm, user["id"]))
            session["username"] = new_username
            flash("Profile updated!", "success")
            return redirect(url_for("profile", username=new_username))
        else:
            execute("UPDATE users SET bio=?,avatar=?,anon_name=? WHERE id=?",
                    (bio, avatar, anon_nm, user["id"]))
            flash("Profile updated!", "success")
            return redirect(url_for("profile", username=user["username"]))

    anon_name = user["anon_name"] or get_anon_name(user["id"])
    return render_template("edit_profile.html", user=user, anon_name=anon_name)

# ─── Follow ───────────────────────────────────────────────────────────────────
@app.route("/follow/<int:uid>", methods=["POST"])
@login_required
def follow(uid):
    viewer = current_user()
    if viewer["id"] == uid:
        return jsonify({"error": "Cannot follow yourself"}), 400

    exists = query("SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?",
                   (viewer["id"], uid), one=True)
    if exists:
        execute("DELETE FROM follows WHERE follower_id=? AND followee_id=?", (viewer["id"], uid))
        following = False
    else:
        execute("INSERT INTO follows (follower_id,followee_id,created_at) VALUES (?,?,?)",
                (viewer["id"], uid, _now()))
        following = True
        # Notify
        target = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if target:
            display = get_anon_name(viewer["id"])  # keep follower anon
            push_notif(uid, f"{display} started following you",
                       url_for("profile", username=target["username"]), "follow")

    count = query("SELECT COUNT(*) as c FROM follows WHERE followee_id=?", (uid,), one=True)["c"]
    return jsonify({"following": following, "count": count})

# ─── People ───────────────────────────────────────────────────────────────────
# ─── Global Search ─────────────────────────────────────────────────────────────
@app.route("/search")
@login_required
def global_search():
    return redirect(url_for("explore"))

@app.route("/explore")
@login_required
def explore():
    viewer = current_user()
    q    = request.args.get("q", "").strip()
    tab  = request.args.get("tab", "people")  # people | channels | posts

    like = f"%{q.lower()}%" if q else "%"

    # ── People ──
    if q:
        users = query(
            """SELECT u.id, u.username, u.role, u.avatar, u.year_group, u.bio,
                      (SELECT COUNT(*) FROM follows WHERE followee_id=u.id) as followers,
                      (SELECT 1 FROM follows WHERE follower_id=? AND followee_id=u.id) as i_follow
               FROM users u WHERE u.id!=? AND (LOWER(u.username) LIKE ? OR LOWER(u.year_group) LIKE ?)
               ORDER BY followers DESC LIMIT 30""",
            (viewer["id"], viewer["id"], like, like)
        )
    else:
        users = query(
            """SELECT u.id, u.username, u.role, u.avatar, u.year_group, u.bio,
                      (SELECT COUNT(*) FROM follows WHERE followee_id=u.id) as followers,
                      (SELECT 1 FROM follows WHERE follower_id=? AND followee_id=u.id) as i_follow
               FROM users u WHERE u.id!=?
               ORDER BY followers DESC, u.username LIMIT 50""",
            (viewer["id"], viewer["id"])
        )

    # ── Channels ──
    if q:
        channels = query(
            """SELECT ch.id, ch.name, ch.description, u.username as creator_name,
                      (SELECT COUNT(*) FROM channel_follows WHERE channel_id=ch.id) as follower_count,
                      (SELECT COUNT(*) FROM channel_posts   WHERE channel_id=ch.id) as post_count,
                      EXISTS(SELECT 1 FROM channel_follows WHERE channel_id=ch.id AND user_id=?) as is_following
               FROM channels ch JOIN users u ON ch.creator_id=u.id
               WHERE LOWER(ch.name) LIKE ? OR LOWER(COALESCE(ch.description,'')) LIKE ?
               ORDER BY follower_count DESC LIMIT 30""",
            (viewer["id"], like, like)
        )
    else:
        channels = query(
            """SELECT ch.id, ch.name, ch.description, u.username as creator_name,
                      (SELECT COUNT(*) FROM channel_follows WHERE channel_id=ch.id) as follower_count,
                      (SELECT COUNT(*) FROM channel_posts   WHERE channel_id=ch.id) as post_count,
                      EXISTS(SELECT 1 FROM channel_follows WHERE channel_id=ch.id AND user_id=?) as is_following
               FROM channels ch JOIN users u ON ch.creator_id=u.id
               ORDER BY follower_count DESC, ch.created_at DESC LIMIT 50""",
            (viewer["id"],)
        )

    # ── Posts (public, non-anon only) ──
    if q:
        posts = query(
            """SELECT p.id, p.content, p.created_at, p.media_type, p.media_path,
                      u.username, u.avatar,
                      (SELECT COUNT(*) FROM reactions WHERE post_id=p.id AND emoji='❤️') as likes,
                      (SELECT COUNT(*) FROM comments  WHERE post_id=p.id) as comment_count
               FROM posts p JOIN users u ON p.author_id=u.id
               WHERE LOWER(p.content) LIKE ? AND p.is_anon=0
               ORDER BY p.created_at DESC LIMIT 20""",
            (like,)
        )
    else:
        posts = query(
            """SELECT p.id, p.content, p.created_at, p.media_type, p.media_path,
                      u.username, u.avatar,
                      (SELECT COUNT(*) FROM reactions WHERE post_id=p.id AND emoji='❤️') as likes,
                      (SELECT COUNT(*) FROM comments  WHERE post_id=p.id) as comment_count
               FROM posts p JOIN users u ON p.author_id=u.id
               WHERE p.is_anon=0
               ORDER BY p.created_at DESC LIMIT 40""",
            ()
        )

    return render_template("explore.html", q=q, tab=tab,
                           users=users, channels=channels, posts=posts)


@app.route("/people")
@login_required
def people():
    q = request.args.get("q", "")
    return redirect(url_for("explore", q=q, tab="people") if q else url_for("explore", tab="people"))

# ─── Notifications ────────────────────────────────────────────────────────────
@app.route("/notifications")
@login_required
def notifications():
    user   = current_user()
    notifs = query(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 60",
        (user["id"],)
    )
    # Mark all read
    execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
    return render_template("notifications.html", notifs=notifs)

# ─── Messages ─────────────────────────────────────────────────────────────────
@app.route("/messages")
@login_required
def messages():
    user  = current_user()
    convs = query(
        """SELECT c.*,
           CASE WHEN c.user_a=? THEN ub.username ELSE ua.username END as other_name,
           CASE WHEN c.user_a=? THEN ub.username ELSE ua.username END as other_username,
           CASE WHEN c.user_a=? THEN ub.avatar  ELSE ua.avatar  END as other_avatar,
           CASE WHEN c.user_a=? THEN ub.id      ELSE ua.id      END as other_id,
           (SELECT content FROM messages m WHERE m.conversation_id=c.id ORDER BY m.created_at DESC LIMIT 1) as last_msg,
           (SELECT created_at FROM messages m WHERE m.conversation_id=c.id ORDER BY m.created_at DESC LIMIT 1) as last_at,
           (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.sender_id!=? AND m.is_read=0) as unread_ct
           FROM conversations c
           JOIN users ua ON c.user_a=ua.id
           JOIN users ub ON c.user_b=ub.id
           WHERE c.user_a=? OR c.user_b=?
           ORDER BY last_at DESC NULLS LAST""",
        (user["id"],)*7
    )
    return render_template("messages.html", convs=convs)

@app.route("/messages/<conv_id>", methods=["GET","POST"])
@login_required
def conversation(conv_id):
    user = current_user()
    conv = query("SELECT * FROM conversations WHERE id=?", (conv_id,), one=True)
    if not conv or (conv["user_a"] != user["id"] and conv["user_b"] != user["id"]):
        abort(403)

    other_id = conv["user_b"] if conv["user_a"] == user["id"] else conv["user_a"]
    other    = query("SELECT * FROM users WHERE id=?", (other_id,), one=True)

    if request.method == "POST":
        content  = request.form.get("content","").strip()
        is_anon  = bool(request.form.get("is_anon"))
        msg_type = request.form.get("msg_type", "text")
        voice_url = ""

        # Handle voice message upload
        if msg_type == "voice" and "voice_file" in request.files:
            vf = request.files["voice_file"]
            if vf and vf.filename:
                vurl, _, _ = save_upload(vf, force_raw=True)
                voice_url = vurl
                content   = content or "🎤 Voice message"

        if content or voice_url:
            reply_to      = request.form.get("reply_to","").strip()
            reply_preview = request.form.get("reply_preview","").strip()[:80]
            execute(
                "INSERT INTO messages (id,conversation_id,sender_id,content,is_anon,is_read,voice_url,msg_type,reply_to,reply_preview,created_at) VALUES (?,?,?,?,?,0,?,?,?,?,?)",
                (_uid(), conv_id, user["id"], content, 1 if is_anon else 0, voice_url, msg_type, reply_to, reply_preview, _now())
            )
            display = get_anon_name(user["id"]) if is_anon else user["username"]
            push_notif(other_id, f"New message from {display}",
                       url_for("conversation", conv_id=conv_id), "message")
        return redirect(url_for("conversation", conv_id=conv_id))

    msgs = query(
        """SELECT m.id, m.conversation_id, m.sender_id, m.content, m.is_anon,
                  m.is_read, m.created_at,
                  COALESCE(m.voice_url, '') as voice_url,
                  COALESCE(m.msg_type, 'text') as msg_type,
                  COALESCE(m.reply_to, '') as reply_to,
                  COALESCE(m.reply_preview, '') as reply_preview,
                  u.username, u.avatar, u.anon_name
           FROM messages m
           JOIN users u ON m.sender_id=u.id
           WHERE m.conversation_id=? ORDER BY m.created_at ASC""",
        (conv_id,)
    )
    # Mark as read
    execute("UPDATE messages SET is_read=1 WHERE conversation_id=? AND sender_id!=?",
            (conv_id, user["id"]))
    return render_template("conversation.html", conv=conv, other=other, msgs=msgs)

@app.route("/messages/<conv_id>/voice", methods=["POST"])
@login_required
def send_voice(conv_id):
    user = current_user()
    conv = query("SELECT * FROM conversations WHERE id=?", (conv_id,), one=True)
    if not conv or (conv["user_a"] != user["id"] and conv["user_b"] != user["id"]):
        abort(403)
    other_id = conv["user_b"] if conv["user_a"] == user["id"] else conv["user_a"]
    if "voice_file" in request.files:
        vf = request.files["voice_file"]
        if vf and vf.filename:
            vurl, _, _ = save_upload(vf, force_raw=True)
            execute(
                "INSERT INTO messages (id,conversation_id,sender_id,content,is_anon,is_read,voice_url,msg_type,created_at) VALUES (?,?,?,?,0,0,?,?,?)",
                (_uid(), conv_id, user["id"], "🎤 Voice message", vurl, "voice", _now())
            )
            push_notif(other_id, f"Voice message from {user['username']}",
                       url_for("conversation", conv_id=conv_id), "message")
    from flask import jsonify
    return jsonify({"ok": True})


@app.route("/messages/start/<int:uid>", methods=["POST"])
@login_required
def start_conversation(uid):
    user = current_user()
    if user["id"] == uid:
        return redirect(url_for("messages"))

    a, b = sorted([user["id"], uid])
    existing = query("SELECT id FROM conversations WHERE user_a=? AND user_b=?", (a,b), one=True)
    if existing:
        return redirect(url_for("conversation", conv_id=existing["id"]))

    cid = _uid()
    execute("INSERT INTO conversations (id,user_a,user_b,created_at) VALUES (?,?,?,?)",
            (cid, a, b, _now()))
    return redirect(url_for("conversation", conv_id=cid))

# ─── Settings ─────────────────────────────────────────────────────────────────
@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    user = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "change_password":
            old_pw  = request.form.get("old_password","")
            new_pw  = request.form.get("new_password","")
            conf_pw = request.form.get("confirm_password","")
            fresh   = query("SELECT password FROM users WHERE id=?", (user["id"],), one=True)
            if fresh["password"] != _hash(old_pw):
                flash("Current password is incorrect.", "error")
            elif new_pw != conf_pw:
                flash("New passwords do not match.", "error")
            elif len(new_pw) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                execute("UPDATE users SET password=? WHERE id=?", (_hash(new_pw), user["id"]))
                flash("Password changed successfully!", "success")

        elif action == "reset_anon":
            new_anon = f"Ghost_{secrets.token_hex(3).upper()}"
            execute("UPDATE users SET anon_name=? WHERE id=?", (new_anon, user["id"]))
            flash(f"New anonymous name: {new_anon}", "success")

    user      = current_user()
    anon_name = user["anon_name"] or get_anon_name(user["id"])
    return render_template("settings.html", user=user, anon_name=anon_name)

# ─── Admin Panel ──────────────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@roles_required("admin","super_admin")
def admin_panel():
    users = query("SELECT * FROM users ORDER BY role, name")
    flagged = query(
        """SELECT p.*, u.username as uname, u.username as uusername
           FROM posts p JOIN users u ON p.author_id=u.id
           WHERE p.flagged=1 ORDER BY p.created_at DESC"""
    )
    flagged_comments = query(
        """SELECT c.*, u.username as uname, u.username as uusername, u.role as urole,
                  p.content as post_content, p.id as post_id, p.recipient as post_recipient
           FROM comments c
           JOIN users u ON c.author_id=u.id
           JOIN posts p ON c.post_id=p.id
           WHERE c.flagged=1 ORDER BY c.created_at DESC"""
    )
    stats = {
        "users":           query("SELECT COUNT(*) as c FROM users",                    one=True)["c"],
        "posts":           query("SELECT COUNT(*) as c FROM posts",                    one=True)["c"],
        "flagged":         query("SELECT COUNT(*) as c FROM posts WHERE flagged=1",    one=True)["c"],
        "flagged_comments":query("SELECT COUNT(*) as c FROM comments WHERE flagged=1", one=True)["c"],
        "anon":            query("SELECT COUNT(*) as c FROM posts WHERE is_anon=1",    one=True)["c"],
        "messages":        query("SELECT COUNT(*) as c FROM messages",                 one=True)["c"],
        "follows":         query("SELECT COUNT(*) as c FROM follows",                  one=True)["c"],
    }
    return render_template("admin.html", users=users, flagged=flagged,
                           flagged_comments=flagged_comments, stats=stats,
                           ROLES=ROLES, ROLE_RECIPIENTS=ROLE_RECIPIENTS,
                           RECIPIENT_LABELS=RECIPIENT_LABELS)

@app.route("/admin/user/create", methods=["POST"])
@login_required
@roles_required("super_admin")
def admin_create_user():
    uname = request.form.get("username","").strip().lower()
    name  = request.form.get("name","").strip()
    pw    = request.form.get("password","")
    role  = request.form.get("role","student")
    if not all([uname, name, pw]):
        flash("All fields are required.", "error")
        return redirect(url_for("admin_panel"))
    if role not in ROLES:
        role = "student"
    anon = f"Ghost_{secrets.token_hex(3).upper()}"
    year_group = request.form.get("year_group", "") if role == "student" else ""
    try:
        execute(
            "INSERT INTO users (username,password,name,role,anon_name,year_group,created_at) VALUES (?,?,?,?,?,?,?)",
            (uname, _hash(pw), name, role, anon, year_group, _now())
        )
        flash(f"User @{uname} created.", "success")
    except Exception:
        flash("Username already exists.", "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:uid>/delete", methods=["POST"])
@login_required
@roles_required("super_admin")
def admin_delete_user(uid):
    u = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if u and u["role"] == "super_admin":
        flash("Cannot delete super admin.", "error")
        return redirect(url_for("admin_panel"))
    execute("DELETE FROM users WHERE id=?", (uid,))
    flash("User deleted.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:uid>/reset-password", methods=["POST"])
@login_required
@roles_required("super_admin")
def admin_reset_password(uid):
    new_pw = request.form.get("new_password","")
    if not new_pw or len(new_pw) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("admin_panel"))
    execute("UPDATE users SET password=? WHERE id=?", (_hash(new_pw), uid))
    flash("Password reset.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:uid>/change-role", methods=["POST"])
@login_required
@roles_required("super_admin")
def admin_change_role(uid):
    new_role = request.form.get("role","student")
    u = query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if u and u["role"] == "super_admin":
        flash("Cannot change super admin role.", "error")
        return redirect(url_for("admin_panel"))
    if new_role not in ROLES:
        new_role = "student"
    execute("UPDATE users SET role=? WHERE id=?", (new_role, uid))
    flash("Role updated.", "success")
    return redirect(url_for("admin_panel"))

# ─── Reels ────────────────────────────────────────────────────────────────────
@app.route("/reels")
@login_required
def reels():
    user = current_user()
    role = user["role"]
    visible = ROLE_VISIBLE.get(role, ["all_school", "announcement"])

    if visible is None:
        rows = query(
            "SELECT * FROM posts WHERE media_type='video' ORDER BY created_at DESC LIMIT 50"
        )
    else:
        placeholders = ",".join("?" * len(visible))
        rows = query(
            f"SELECT * FROM posts WHERE media_type='video' AND recipient IN ({placeholders}) ORDER BY created_at DESC LIMIT 50",
            visible
        )

    reels_list = [serialise_post(r, user["id"]) for r in rows]
    return render_template("reels.html", reels=reels_list)


@app.route("/media/<path:filename>")
def serve_media(filename):
    # Legacy route — Cloudinary files are served directly by URL.
    # This handles any old relative filenames stored in DB before migration.
    if filename.startswith("http"):
        return redirect(filename)
    # Try Cloudinary URL reconstruction for old records
    cloud = os.environ.get("CLOUDINARY_CLOUD_NAME","")
    if cloud:
        return redirect(f"https://res.cloudinary.com/{cloud}/image/upload/eia_voice/{filename}")
    abort(404)


@app.route("/test-download")
@login_required  
def test_download():
    """Debug route - tests Cloudinary connectivity step by step."""
    import urllib.request as _urlreq
    import cloudinary.utils
    import traceback
    results = {}

    # Step 1: Check env vars
    cloud = os.environ.get("CLOUDINARY_CLOUD_NAME","")
    key   = os.environ.get("CLOUDINARY_API_KEY","")
    sec   = os.environ.get("CLOUDINARY_API_SECRET","")
    results["env"] = {
        "cloud_name": cloud[:6]+"..." if cloud else "MISSING",
        "api_key":    key[:6]+"..."   if key   else "MISSING",
        "api_secret": sec[:6]+"..."   if sec   else "MISSING",
    }

    # Step 2: Try generating a signed URL
    try:
        signed, _ = cloudinary.utils.cloudinary_url(
            "eia_voice/test",
            resource_type="raw", type="upload",
            sign_url=True, secure=True,
        )
        results["signed_url"] = signed[:80]+"..."
    except Exception as e:
        results["signed_url_error"] = str(e)

    # Step 3: Try fetching a real file from DB
    try:
        row = query("SELECT media_path, media_name FROM posts WHERE media_type='document' LIMIT 1", one=True)
        if not row:
            row = query("SELECT media_path, media_name FROM channel_posts WHERE media_type='document' LIMIT 1", one=True)
        if row:
            results["sample_url"] = row["media_path"]
            results["sample_name"] = row["media_name"]
            # Try fetching it
            import re as _re
            m = _re.search(r'/upload/(?:v\d+/)?(.+?)(?:\?|$)', row["media_path"])
            if m:
                pid = m.group(1)
                results["public_id"] = pid
                # Test private_download_url
                ext2 = pid.rsplit(".", 1)[-1] if "." in pid else "bin"
                dl_url = cloudinary.utils.private_download_url(
                    pid, ext2, resource_type="raw", type="upload", attachment=True,
                )
                results["private_dl_url"] = dl_url[:120]
                req = _urlreq.Request(dl_url, headers={"User-Agent":"Mozilla/5.0"})
                with _urlreq.urlopen(req, timeout=15) as resp:
                    data = resp.read(100)
                results["fetch_ok"] = True
                results["bytes_preview"] = repr(data[:20])
            else:
                results["parse_error"] = "Could not extract public_id"
        else:
            results["no_doc"] = "No documents in DB yet"
    except Exception as e:
        results["fetch_error"] = str(e)
        results["traceback"] = traceback.format_exc()

    from flask import jsonify
    return jsonify(results)


@app.route("/stream-voice")
@login_required
def stream_voice():
    """Proxy Cloudinary raw audio so browser can play without CORS/auth issues."""
    from urllib.parse import unquote
    import urllib.request as _urlreq
    import cloudinary.utils, re as _re

    url = unquote(request.args.get("url", ""))
    if not url.startswith("http"):
        abort(400)

    # Detect extension for mime type
    ext_match = _re.search(r'\.(\w+)(?:\?|$)', url)
    ext = ext_match.group(1).lower() if ext_match else "webm"
    mime_map = {"webm":"audio/webm","ogg":"audio/ogg","m4a":"audio/mp4","wav":"audio/wav","mp3":"audio/mpeg"}
    mime = mime_map.get(ext, "audio/webm")

    try:
        # Generate signed URL for Cloudinary raw resource
        m = _re.search(r"/upload/(?:v\d+/)?(.+?)(?:\?|$)", url)
        if m:
            public_id = m.group(1)
            signed_url = cloudinary.utils.private_download_url(
                public_id, ext,
                resource_type="raw", type="upload", attachment=False,
            )
            fetch_url = signed_url
        else:
            fetch_url = url

        req = _urlreq.Request(fetch_url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlreq.urlopen(req, timeout=30) as resp:
            data = resp.read()

        from flask import Response as FR
        r = FR(data, mimetype=mime)
        r.headers["Accept-Ranges"]  = "bytes"
        r.headers["Content-Length"] = str(len(data))
        r.headers["Cache-Control"]  = "private, max-age=3600"
        return r

    except Exception as e:
        return str(e), 500, {"Content-Type": "text/plain"}


@app.route("/download")
@login_required
def download_media():
    from urllib.parse import unquote
    import urllib.request as _urlreq
    import re as _re

    url       = unquote(request.args.get("url", ""))
    orig_name = unquote(request.args.get("name", "document"))

    if not url.startswith("http"):
        abort(400)

    mime_map = {
        "pdf":  "application/pdf",
        "doc":  "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "ppt":  "application/vnd.ms-powerpoint",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xls":  "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "txt":  "text/plain",
    }
    ext  = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else ""
    mime = mime_map.get(ext, "application/octet-stream")

    try:
        # Use Cloudinary's private_download_url which generates a
        # time-limited authenticated download URL via the API
        import cloudinary.utils
        m = _re.search(r"/upload/(?:v\d+/)?(.+?)(?:\?|$)", url)
        if not m:
            return f"Cannot parse public_id from: {url}", 500, {"Content-Type": "text/plain"}
        public_id = m.group(1)

        # Generate a private download URL (works for raw/authenticated resources)
        dl_url = cloudinary.utils.private_download_url(
            public_id,
            ext if ext else "bin",
            resource_type = "raw",
            type          = "upload",
            attachment    = True,
        )

        req = _urlreq.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlreq.urlopen(req, timeout=30) as resp:
            data = resp.read()

        from flask import Response as FR
        r = FR(data, mimetype=mime)
        r.headers["Content-Disposition"] = 'attachment; filename="' + orig_name + '"'
        r.headers["Content-Length"]      = str(len(data))
        r.headers["Cache-Control"]       = "no-cache"
        return r

    except Exception as e:
        return str(e), 500, {"Content-Type": "text/plain"}


@app.route("/channels")
@login_required
def channels():
    user = current_user()
    uid  = user["id"]

    # Channels user follows
    my_channels = query(
        """SELECT ch.*, u.username as creator_name,
                  (SELECT COUNT(*) FROM channel_follows WHERE channel_id=ch.id) as follower_count,
                  (SELECT COUNT(*) FROM channel_posts WHERE channel_id=ch.id) as post_count
           FROM channels ch
           JOIN channel_follows cf ON cf.channel_id=ch.id
           JOIN users u ON ch.creator_id=u.id
           WHERE cf.user_id=?
           ORDER BY ch.name""", (uid,)
    )

    # Channels user created (not already in above list)
    my_created = query(
        """SELECT ch.*, u.username as creator_name,
                  (SELECT COUNT(*) FROM channel_follows WHERE channel_id=ch.id) as follower_count,
                  (SELECT COUNT(*) FROM channel_posts WHERE channel_id=ch.id) as post_count
           FROM channels ch JOIN users u ON ch.creator_id=u.id
           WHERE ch.creator_id=?
           ORDER BY ch.name""", (uid,)
    )

    # All channels for discovery
    all_channels = query(
        """SELECT ch.*, u.username as creator_name,
                  (SELECT COUNT(*) FROM channel_follows WHERE channel_id=ch.id) as follower_count,
                  (SELECT COUNT(*) FROM channel_posts WHERE channel_id=ch.id) as post_count,
                  EXISTS(SELECT 1 FROM channel_follows WHERE channel_id=ch.id AND user_id=?) as is_following
           FROM channels ch JOIN users u ON ch.creator_id=u.id
           ORDER BY follower_count DESC, ch.created_at DESC""", (uid,)
    )

    return render_template("channels.html", my_channels=my_channels,
                           my_created=my_created, all_channels=all_channels)


@app.route("/channels/create", methods=["POST"])
@login_required
def create_channel():
    user = current_user()
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()

    if not name:
        flash("Channel name is required.", "error")
        return redirect(url_for("channels"))

    # Clean name: letters, numbers, underscores, hyphens only
    import re as _re
    name = _re.sub(r'[^a-zA-Z0-9_\-]', '', name.replace(' ', '_'))
    if not name:
        flash("Channel name must contain letters or numbers.", "error")
        return redirect(url_for("channels"))

    try:
        execute(
            "INSERT INTO channels (name, description, creator_id, created_at) VALUES (?,?,?,?)",
            (name, desc, user["id"], _now())
        )
        ch = query("SELECT id FROM channels WHERE name=?", (name,), one=True)
        if ch:
            # Creator auto-follows their channel
            execute(
                "INSERT INTO channel_follows (channel_id, user_id, joined_at) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (ch["id"], user["id"], _now())
            )
            flash(f"Channel #{name} created!", "success")
            return redirect(url_for("channel_detail", cid=ch["id"]))
    except Exception:
        flash("A channel with that name already exists.", "error")

    return redirect(url_for("channels"))


@app.route("/channels/<int:cid>")
@login_required
def channel_detail(cid):
    user = current_user()
    uid  = user["id"]

    ch = query(
        """SELECT ch.*, u.username as creator_name,
                  (SELECT COUNT(*) FROM channel_follows WHERE channel_id=ch.id) as follower_count,
                  EXISTS(SELECT 1 FROM channel_follows WHERE channel_id=ch.id AND user_id=?) as is_following
           FROM channels ch JOIN users u ON ch.creator_id=u.id
           WHERE ch.id=?""", (uid, cid), one=True
    )
    if not ch:
        abort(404)

    posts = query(
        """SELECT cp.id, cp.channel_id, cp.author_id, cp.content,
                  cp.media_path, cp.media_type, cp.is_anon, cp.created_at,
                  COALESCE(cp.media_name, '') as media_name,
                  u.username as author_name, u.role as author_role,
                  u.avatar as author_avatar, u.anon_name as author_anon,
                  (SELECT COUNT(*) FROM channel_comments WHERE post_id=cp.id) as comment_count
           FROM channel_posts cp JOIN users u ON cp.author_id=u.id
           WHERE cp.channel_id=?
           ORDER BY cp.created_at DESC LIMIT 60""", (cid,)
    )

    posts_with_comments = []
    for p in posts:
        is_anon  = bool(p["is_anon"])
        display  = p["author_anon"] or f"Ghost_{p['author_id']}" if is_anon else p["author_name"]
        avatar   = "" if is_anon else p["author_avatar"]
        cmts = query(
            """SELECT cc.*, u.username as author_name, u.anon_name, u.avatar
               FROM channel_comments cc JOIN users u ON cc.author_id=u.id
               WHERE cc.post_id=? ORDER BY cc.created_at ASC""", (p["id"],)
        )
        comments_out = []
        for c in cmts:
            c_anon = bool(c["is_anon"])
            comments_out.append({
                "id": c["id"], "content": c["content"],
                "display": c["anon_name"] if c_anon else c["author_name"],
                "avatar": "" if c_anon else c["avatar"],
                "is_anon": c_anon, "created_at": c["created_at"],
                "author_id": c["author_id"],
            })
        posts_with_comments.append({
            "id": p["id"], "content": p["content"],
            "media_path": p["media_path"], "media_type": p["media_type"],
            "media_name": p["media_name"] if "media_name" in p.keys() else "",
            "display": display, "avatar": avatar,
            "author_role": p["author_role"], "author_id": p["author_id"],
            "is_anon": is_anon,
            "comment_count": p["comment_count"],
            "created_at": p["created_at"],
            "comments": comments_out,
        })

    return render_template("channel_detail.html", ch=ch, posts=posts_with_comments, cu=user)


@app.route("/channels/<int:cid>/follow", methods=["POST"])
@login_required
def channel_follow(cid):
    user = current_user()
    existing = query("SELECT 1 FROM channel_follows WHERE channel_id=? AND user_id=?", (cid, user["id"]), one=True)
    if existing:
        execute("DELETE FROM channel_follows WHERE channel_id=? AND user_id=?", (cid, user["id"]))
        return jsonify({"following": False})
    else:
        execute("INSERT INTO channel_follows (channel_id, user_id, joined_at) VALUES (?,?,?)", (cid, user["id"], _now()))
        return jsonify({"following": True})


@app.route("/channels/<int:cid>/post", methods=["POST"])
@login_required
def channel_post(cid):
    user = current_user()
    ch   = query("SELECT * FROM channels WHERE id=?", (cid,), one=True)
    if not ch:
        abort(404)

    # Only followers and the creator can post
    is_member = query("SELECT 1 FROM channel_follows WHERE channel_id=? AND user_id=?", (cid, user["id"]), one=True)
    if not is_member and ch["creator_id"] != user["id"]:
        flash("You must follow this channel to post in it.", "error")
        return redirect(url_for("channel_detail", cid=cid))

    content = request.form.get("content", "").strip()
    is_anon = bool(request.form.get("is_anon"))

    media_path, media_type, media_name = "", "", ""
    if "media" in request.files:
        f = request.files["media"]
        if f and f.filename:
            media_path, media_type, media_name = save_upload(f)

    if not content and not media_path:
        flash("Post cannot be empty.", "error")
        return redirect(url_for("channel_detail", cid=cid))

    pid = _uid()
    execute(
        "INSERT INTO channel_posts (id,channel_id,author_id,content,media_path,media_type,media_name,is_anon,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, cid, user["id"], content, media_path, media_type, media_name, 1 if is_anon else 0, _now())
    )

    # Notify all followers
    followers = query("SELECT user_id FROM channel_follows WHERE channel_id=? AND user_id!=?", (cid, user["id"]))
    display   = get_anon_name(user["id"]) if is_anon else user["username"]
    for fl in followers:
        push_notif(fl["user_id"],
                   f"New post in #{ch['name']} by {display}",
                   url_for("channel_detail", cid=cid),
                   notif_type="info", actor_id=user["id"])

    return redirect(url_for("channel_detail", cid=cid))


@app.route("/channels/<int:cid>/comment/<pid>", methods=["POST"])
@login_required
def channel_comment(cid, pid):
    user    = current_user()
    content = request.form.get("content", "").strip()
    is_anon = bool(request.form.get("is_anon"))
    if not content:
        return redirect(url_for("channel_detail", cid=cid))
    cmt_id = _uid()
    execute(
        "INSERT INTO channel_comments (id,post_id,author_id,content,is_anon,created_at) VALUES (?,?,?,?,?,?)",
        (cmt_id, pid, user["id"], content, 1 if is_anon else 0, _now())
    )
    return redirect(url_for("channel_detail", cid=cid))


@app.route("/channels/post/<pid>/delete", methods=["POST"])
@login_required
def delete_channel_post(pid):
    user = current_user()
    p    = query("SELECT * FROM channel_posts WHERE id=?", (pid,), one=True)
    if not p:
        abort(404)
    ch = query("SELECT * FROM channels WHERE id=?", (p["channel_id"],), one=True)
    if p["author_id"] != user["id"] and (ch and ch["creator_id"] != user["id"]) and user["role"] not in ("admin","super_admin"):
        abort(403)
    cid = p["channel_id"]
    execute("DELETE FROM channel_posts WHERE id=?", (pid,))
    return redirect(url_for("channel_detail", cid=cid))


@app.route("/channels/<int:cid>/delete", methods=["POST"])
@login_required
def delete_channel(cid):
    user = current_user()
    ch   = query("SELECT * FROM channels WHERE id=?", (cid,), one=True)
    if not ch:
        abort(404)
    if ch["creator_id"] != user["id"] and user["role"] not in ("admin","super_admin"):
        abort(403)
    execute("DELETE FROM channels WHERE id=?", (cid,))
    flash("Channel deleted.", "success")
    return redirect(url_for("channels"))

YEAR_GROUPS = ["Year 8", "Year 9", "Year 10", "Year 11", "Year 12", "Year 13"]
POST_TYPES  = {"note": "📄 Note", "paper": "📝 Paper / Assignment", "announcement": "📢 Announcement"}

# ═══════════════════════════════════════════════════════════════════════════════
# CLASSROOM ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/classes")
@login_required
def classes():
    user = current_user()
    role = user["role"]

    if role in ("teacher", "super_admin"):
        # Teachers see classes they own
        my_classes = query(
            "SELECT c.*, u.username as teacher_name FROM classes c JOIN users u ON c.teacher_id=u.id "
            "WHERE c.teacher_id=? ORDER BY c.year_group, c.name", (user["id"],)
        )
        # Admins / super_admin see ALL classes
        if role == "super_admin":
            my_classes = query(
                "SELECT c.*, u.username as teacher_name FROM classes c JOIN users u ON c.teacher_id=u.id "
                "ORDER BY c.year_group, c.name"
            )
        other_classes = []
    elif role == "admin":
        my_classes = query(
            "SELECT c.*, u.username as teacher_name FROM classes c JOIN users u ON c.teacher_id=u.id "
            "ORDER BY c.year_group, c.name"
        )
        other_classes = []
    else:
        # Students see classes they are enrolled in
        my_classes = query(
            """SELECT c.*, u.username as teacher_name FROM classes c
               JOIN class_members cm ON cm.class_id=c.id
               JOIN users u ON c.teacher_id=u.id
               WHERE cm.student_id=? ORDER BY c.year_group, c.name""",
            (user["id"],)
        )
        other_classes = []

    # Attach unread reply counts for teachers
    for cls in my_classes:
        cnt = query(
            "SELECT COUNT(*) as c FROM class_posts WHERE class_id=?", (cls["id"],), one=True
        )
        cls = dict(cls)

    return render_template("classes.html", my_classes=my_classes,
                           other_classes=other_classes, YEAR_GROUPS=YEAR_GROUPS,
                           POST_TYPES=POST_TYPES)


@app.route("/classes/create", methods=["POST"])
@login_required
@roles_required("teacher", "admin", "super_admin")
def create_class():
    user    = current_user()
    name    = request.form.get("name", "").strip()
    subject = request.form.get("subject", "").strip()
    yg      = request.form.get("year_group", "Year 8")

    if not name:
        flash("Class name is required.", "error")
        return redirect(url_for("classes"))

    if yg not in YEAR_GROUPS:
        yg = "Year 8"

    # Admins can assign a teacher; teachers create for themselves
    teacher_id = user["id"]
    if user["role"] in ("admin", "super_admin"):
        tid = request.form.get("teacher_id")
        if tid:
            teacher_id = int(tid)

    execute(
        "INSERT INTO classes (name, subject, year_group, teacher_id, created_at) VALUES (?,?,?,?,?)",
        (name, subject, yg, teacher_id, _now())
    )
    flash(f"Class '{name}' created.", "success")
    return redirect(url_for("classes"))


@app.route("/classes/<int:cid>")
@login_required
def class_detail(cid):
    user = current_user()
    cls  = query("SELECT c.*, u.username as teacher_name, u.id as tid FROM classes c JOIN users u ON c.teacher_id=u.id WHERE c.id=?", (cid,), one=True)
    if not cls:
        abort(404)

    role = user["role"]

    # Access control: students must be members, teachers must own or be admin
    if role == "student":
        member = query("SELECT 1 FROM class_members WHERE class_id=? AND student_id=?", (cid, user["id"]), one=True)
        if not member:
            abort(403)
    elif role == "teacher":
        if cls["teacher_id"] != user["id"]:
            abort(403)
    # admins and super_admin can see all

    posts = query(
        """SELECT cp.*, u.username as author_name, u.role as author_role
           FROM class_posts cp JOIN users u ON cp.author_id=u.id
           WHERE cp.class_id=? ORDER BY cp.created_at DESC""",
        (cid,)
    )

    # Attach replies to each post
    posts_with_replies = []
    for p in posts:
        replies = query(
            """SELECT cr.*, u.username as author_name, u.role as author_role
               FROM class_replies cr JOIN users u ON cr.author_id=u.id
               WHERE cr.post_id=? ORDER BY cr.created_at ASC""",
            (p["id"],)
        )
        posts_with_replies.append({"post": p, "replies": replies})

    # Members list (for teacher/admin view)
    members = []
    if role in ("teacher", "admin", "super_admin"):
        members = query(
            """SELECT u.* FROM users u
               JOIN class_members cm ON cm.student_id=u.id
               WHERE cm.class_id=? ORDER BY u.username""",
            (cid,)
        )

    # All students for enrollment (admin/teacher)
    all_students = []
    if role in ("teacher", "admin", "super_admin"):
        enrolled_ids = [m["id"] for m in members]
        all_students = query(
            "SELECT * FROM users WHERE role='student' ORDER BY year_group, username"
        )
        all_students = [s for s in all_students if s["id"] not in enrolled_ids]

    return render_template("class_detail.html", cls=cls, posts=posts_with_replies,
                           members=members, all_students=all_students,
                           POST_TYPES=POST_TYPES, YEAR_GROUPS=YEAR_GROUPS)


@app.route("/classes/<int:cid>/post", methods=["POST"])
@login_required
@roles_required("teacher", "admin", "super_admin")
def class_post(cid):
    user  = current_user()
    cls   = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls:
        abort(404)
    if user["role"] == "teacher" and cls["teacher_id"] != user["id"]:
        abort(403)

    title     = request.form.get("title", "").strip()
    content   = request.form.get("content", "").strip()
    post_type = request.form.get("post_type", "note")
    if post_type not in POST_TYPES:
        post_type = "note"

    file_path, file_name = "", ""
    if "file" in request.files:
        f = request.files["file"]
        if f and f.filename:
            safe = secure_filename(f.filename)
            url, _, _x = save_upload(f)
            file_path = url
            file_name = safe

    if not content and not file_path:
        flash("Please add some content or attach a file.", "error")
        return redirect(url_for("class_detail", cid=cid))

    pid = _uid()
    execute(
        "INSERT INTO class_posts (id,class_id,author_id,title,content,post_type,file_path,file_name,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, cid, user["id"], title, content, post_type, file_path, file_name, _now())
    )

    # Notify all enrolled students
    members = query("SELECT student_id FROM class_members WHERE class_id=?", (cid,))
    cls_name = cls["name"]
    lbl = POST_TYPES.get(post_type, post_type)
    for m in members:
        push_notif(
            m["student_id"],
            f"{lbl} posted in {cls_name}: {title or content[:40]}",
            url_for("class_detail", cid=cid),
            notif_type="info",
            actor_id=user["id"]
        )

    flash("Posted to class.", "success")
    return redirect(url_for("class_detail", cid=cid))


@app.route("/classes/<int:cid>/reply/<pid>", methods=["POST"])
@login_required
def class_reply(cid, pid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls:
        abort(404)

    # Students must be enrolled
    if user["role"] == "student":
        member = query("SELECT 1 FROM class_members WHERE class_id=? AND student_id=?", (cid, user["id"]), one=True)
        if not member:
            abort(403)

    content   = request.form.get("content", "").strip()
    file_path, file_name = "", ""
    if "file" in request.files:
        f = request.files["file"]
        if f and f.filename:
            safe  = secure_filename(f.filename)
            url, _, _x = save_upload(f)
            file_path = url
            file_name = safe

    if not content and not file_path:
        flash("Reply cannot be empty.", "error")
        return redirect(url_for("class_detail", cid=cid))

    rid = _uid()
    execute(
        "INSERT INTO class_replies (id,post_id,author_id,content,file_path,file_name,created_at) VALUES (?,?,?,?,?,?,?)",
        (rid, pid, user["id"], content, file_path, file_name, _now())
    )

    # Notify teacher of the reply
    post_row = query("SELECT * FROM class_posts WHERE id=?", (pid,), one=True)
    if post_row and user["id"] != cls["teacher_id"]:
        push_notif(
            cls["teacher_id"],
            f"{user['username']} replied in {cls['name']}",
            url_for("class_detail", cid=cid),
            notif_type="info",
            actor_id=user["id"]
        )

    return redirect(url_for("class_detail", cid=cid))


@app.route("/classes/<int:cid>/enroll", methods=["POST"])
@login_required
@roles_required("teacher", "admin", "super_admin")
def class_enroll(cid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls:
        abort(404)
    if user["role"] == "teacher" and cls["teacher_id"] != user["id"]:
        abort(403)

    sid = request.form.get("student_id")
    if sid:
        try:
            execute(
                "INSERT INTO class_members (class_id, student_id, joined_at) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (cid, int(sid), _now())
            )
            # Notify student
            s = query("SELECT username FROM users WHERE id=?", (int(sid),), one=True)
            if s:
                push_notif(int(sid), f"You have been enrolled in {cls['name']}",
                           url_for("class_detail", cid=cid), notif_type="info")
        except Exception:
            pass

    return redirect(url_for("class_detail", cid=cid))


@app.route("/classes/<int:cid>/unenroll/<int:sid>", methods=["POST"])
@login_required
@roles_required("teacher", "admin", "super_admin")
def class_unenroll(cid, sid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls:
        abort(404)
    if user["role"] == "teacher" and cls["teacher_id"] != user["id"]:
        abort(403)
    execute("DELETE FROM class_members WHERE class_id=? AND student_id=?", (cid, sid))
    return redirect(url_for("class_detail", cid=cid))


@app.route("/classes/<int:cid>/delete", methods=["POST"])
@login_required
@roles_required("teacher", "admin", "super_admin")
def delete_class(cid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls:
        abort(404)
    if user["role"] == "teacher" and cls["teacher_id"] != user["id"]:
        abort(403)
    execute("DELETE FROM classes WHERE id=?", (cid,))
    flash("Class deleted.", "success")
    return redirect(url_for("classes"))


@app.route("/classes/post/<pid>/delete", methods=["POST"])
@login_required
@roles_required("teacher", "admin", "super_admin")
def delete_class_post(pid):
    cp = query("SELECT * FROM class_posts WHERE id=?", (pid,), one=True)
    if not cp:
        abort(404)
    execute("DELETE FROM class_posts WHERE id=?", (pid,))
    return redirect(url_for("class_detail", cid=cp["class_id"]))


@app.route("/classes/reply/<rid>/delete", methods=["POST"])
@login_required
def delete_class_reply(rid):
    user  = current_user()
    reply = query("SELECT cr.*, cp.class_id FROM class_replies cr JOIN class_posts cp ON cr.post_id=cp.id WHERE cr.id=?", (rid,), one=True)
    if not reply:
        abort(404)
    if reply["author_id"] != user["id"] and user["role"] not in ("teacher", "admin", "super_admin"):
        abort(403)
    cid = reply["class_id"]
    execute("DELETE FROM class_replies WHERE id=?", (rid,))
    return redirect(url_for("class_detail", cid=cid))


# ─── Entrypoint ───────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════
# YEARBOOK
# ═══════════════════════════════════════════════════════════════════════════════

YEARBOOK_YEARS = ["Year 11", "Year 13"]

@app.route("/yearbook")
@login_required
def yearbook():
    user = current_user()
    yrs = {}
    for yg in YEARBOOK_YEARS:
        meta = query("SELECT * FROM yearbook_years WHERE year_group=?", (yg,), one=True)
        count = query("SELECT COUNT(*) as c FROM users WHERE year_group=? AND role='student'", (yg,), one=True)
        yrs[yg] = {"meta": meta, "count": count["c"] if count else 0}
    return render_template("yearbook.html", yrs=yrs, YEARBOOK_YEARS=YEARBOOK_YEARS)


@app.route("/yearbook/<path:year_group>")
@login_required
def yearbook_detail(year_group):
    if year_group not in YEARBOOK_YEARS:
        abort(404)
    user = current_user()
    meta = query("SELECT * FROM yearbook_years WHERE year_group=?", (year_group,), one=True)

    students = query(
        """SELECT u.id, u.username, u.avatar, u.bio, u.year_group,
                  ye.quote, ye.ambition, ye.memory, ye.nickname
           FROM users u
           LEFT JOIN yearbook_entries ye ON ye.user_id=u.id AND ye.year_group=?
           WHERE u.year_group=? AND u.role='student'
           ORDER BY u.username""",
        (year_group, year_group)
    )
    is_editor = user["role"] in ("admin", "super_admin")
    my_entry  = query("SELECT * FROM yearbook_entries WHERE user_id=? AND year_group=?",
                      (user["id"], year_group), one=True)
    return render_template("yearbook_detail.html",
                           year_group=year_group, meta=meta,
                           students=students, is_editor=is_editor,
                           my_entry=my_entry, cu=user)


@app.route("/yearbook/<path:year_group>/edit-meta", methods=["POST"])
@login_required
@roles_required("admin", "super_admin")
def yearbook_edit_meta(year_group):
    if year_group not in YEARBOOK_YEARS:
        abort(404)
    title        = request.form.get("title", "").strip()
    subtitle     = request.form.get("subtitle", "").strip()
    class_teacher = request.form.get("class_teacher", "").strip()
    message      = request.form.get("message", "").strip()
    achievements = request.form.get("achievements", "").strip()
    cover_color  = request.form.get("cover_color", "#1a2e5a").strip()

    existing = query("SELECT id FROM yearbook_years WHERE year_group=?", (year_group,), one=True)
    if existing:
        execute("""UPDATE yearbook_years SET title=?,subtitle=?,class_teacher=?,message=?,
                   achievements=?,cover_color=?,updated_at=?,updated_by=? WHERE year_group=?""",
                (title, subtitle, class_teacher, message, achievements, cover_color,
                 _now(), current_user()["id"], year_group))
    else:
        execute("""INSERT INTO yearbook_years (year_group,title,subtitle,class_teacher,message,
                   achievements,cover_color,updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?,?)""",
                (year_group, title, subtitle, class_teacher, message, achievements,
                 cover_color, _now(), current_user()["id"]))
    flash("Yearbook details updated.", "success")
    return redirect(url_for("yearbook_detail", year_group=year_group))


@app.route("/yearbook/<path:year_group>/edit-entry", methods=["POST"])
@login_required
def yearbook_edit_entry(year_group):
    if year_group not in YEARBOOK_YEARS:
        abort(404)
    user   = current_user()
    # Students edit their own; admins can edit anyone's
    target_uid = request.form.get("user_id", str(user["id"])).strip()
    if str(user["id"]) != target_uid and user["role"] not in ("admin", "super_admin"):
        abort(403)
    target_uid = int(target_uid)

    quote    = request.form.get("quote", "").strip()[:200]
    ambition = request.form.get("ambition", "").strip()[:200]
    memory   = request.form.get("memory", "").strip()[:200]
    nickname = request.form.get("nickname", "").strip()[:60]

    existing = query("SELECT id FROM yearbook_entries WHERE user_id=? AND year_group=?",
                     (target_uid, year_group), one=True)
    if existing:
        execute("""UPDATE yearbook_entries SET quote=?,ambition=?,memory=?,nickname=?,updated_at=?
                   WHERE user_id=? AND year_group=?""",
                (quote, ambition, memory, nickname, _now(), target_uid, year_group))
    else:
        execute("""INSERT INTO yearbook_entries (user_id,year_group,quote,ambition,memory,nickname,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (target_uid, year_group, quote, ambition, memory, nickname, _now()))
    flash("Entry saved.", "success")
    return redirect(url_for("yearbook_detail", year_group=year_group))


@app.route("/yearbook/<path:year_group>/print")
@login_required
@roles_required("admin", "super_admin")
def yearbook_print(year_group):
    if year_group not in YEARBOOK_YEARS:
        abort(404)
    meta = query("SELECT * FROM yearbook_years WHERE year_group=?", (year_group,), one=True)
    students = query(
        """SELECT u.id, u.username, u.avatar, u.bio, u.year_group,
                  ye.quote, ye.ambition, ye.memory, ye.nickname
           FROM users u
           LEFT JOIN yearbook_entries ye ON ye.user_id=u.id AND ye.year_group=?
           WHERE u.year_group=? AND u.role='student'
           ORDER BY u.username""",
        (year_group, year_group)
    )
    return render_template("yearbook_print.html",
                           year_group=year_group, meta=meta, students=students)


# Run init_db on startup regardless of how the app is launched (gunicorn or direct)
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"[init_db] warning: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  ASSIGNMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/classes/<int:cid>/assignments")
@login_required
def assignments(cid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    asgns = query("SELECT a.*, (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id) as sub_count FROM assignments a WHERE a.class_id=? ORDER BY a.due_date ASC", (cid,))
    my_subs = {}
    if user["role"] == "student":
        subs = query("SELECT * FROM submissions WHERE student_id=?", (user["id"],))
        my_subs = {s["assignment_id"]: s for s in subs}
    members_count = query("SELECT COUNT(*) as c FROM class_members WHERE class_id=?", (cid,), one=True)["c"]
    return render_template("assignments.html", cls=cls, asgns=asgns, my_subs=my_subs, members_count=members_count)

@app.route("/classes/<int:cid>/assignments/create", methods=["POST"])
@login_required
@roles_required("teacher","admin","super_admin")
def create_assignment(cid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    if user["role"] == "teacher" and cls["teacher_id"] != user["id"]: abort(403)
    title    = request.form.get("title","").strip()
    desc     = request.form.get("description","").strip()
    due      = request.form.get("due_date","").strip()
    marks    = int(request.form.get("max_marks", 100) or 100)
    if not title or not due:
        flash("Title and due date are required.", "error")
        return redirect(url_for("assignments", cid=cid))
    execute("INSERT INTO assignments (id,class_id,teacher_id,title,description,due_date,max_marks,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (_uid(), cid, user["id"], title, desc, due, marks, _now()))
    members = query("SELECT student_id FROM class_members WHERE class_id=?", (cid,))
    for m in members:
        push_notif(m["student_id"], f"New assignment in {cls['name']}: {title}", url_for("assignments", cid=cid), "info", user["id"])
    flash("Assignment created.", "success")
    return redirect(url_for("assignments", cid=cid))

@app.route("/assignments/<aid>/submit", methods=["POST"])
@login_required
def submit_assignment(aid):
    user = current_user()
    asgn = query("SELECT * FROM assignments WHERE id=?", (aid,), one=True)
    if not asgn: abort(404)
    content   = request.form.get("content","").strip()
    file_path, file_name = "", ""
    if "file" in request.files:
        f = request.files["file"]
        if f and f.filename:
            from werkzeug.utils import secure_filename
            file_path, _, _ = save_upload(f)
            file_name = secure_filename(f.filename)
    if not content and not file_path:
        flash("Please write something or attach a file.", "error")
        return redirect(url_for("assignments", cid=asgn["class_id"]))
    existing = query("SELECT id FROM submissions WHERE assignment_id=? AND student_id=?", (aid, user["id"]), one=True)
    if existing:
        execute("UPDATE submissions SET content=?,file_path=?,file_name=?,submitted_at=? WHERE id=?",
                (content, file_path, file_name, _now(), existing["id"]))
        flash("Submission updated.", "success")
    else:
        execute("INSERT INTO submissions (id,assignment_id,student_id,content,file_path,file_name,submitted_at) VALUES (?,?,?,?,?,?,?)",
                (_uid(), aid, user["id"], content, file_path, file_name, _now()))
        flash("Submitted!", "success")
    return redirect(url_for("assignments", cid=asgn["class_id"]))

@app.route("/assignments/<aid>/grade/<sid>", methods=["POST"])
@login_required
@roles_required("teacher","admin","super_admin")
def grade_submission(aid, sid):
    grade    = request.form.get("grade","").strip()
    feedback = request.form.get("feedback","").strip()
    sub = query("SELECT * FROM submissions WHERE id=?", (sid,), one=True)
    if not sub: abort(404)
    execute("UPDATE submissions SET grade=?,feedback=?,graded_at=? WHERE id=?", (grade, feedback, _now(), sid))
    asgn = query("SELECT * FROM assignments WHERE id=?", (aid,), one=True)
    push_notif(sub["student_id"], f"Your assignment has been graded: {grade}", url_for("assignments", cid=asgn["class_id"]), "info")
    flash("Graded.", "success")
    return redirect(url_for("view_submissions", aid=aid))

@app.route("/assignments/<aid>/submissions")
@login_required
@roles_required("teacher","admin","super_admin")
def view_submissions(aid):
    asgn = query("SELECT a.*, c.name as class_name FROM assignments a JOIN classes c ON c.id=a.class_id WHERE a.id=?", (aid,), one=True)
    if not asgn: abort(404)
    subs = query("""SELECT s.*, u.username FROM submissions s JOIN users u ON s.student_id=u.id WHERE s.assignment_id=? ORDER BY s.submitted_at""", (aid,))
    return render_template("submissions.html", asgn=asgn, subs=subs)


# ═══════════════════════════════════════════════════════════════════════════════
#  QUIZZES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/classes/<int:cid>/quizzes")
@login_required
def quizzes(cid):
    user  = current_user()
    cls   = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    qlist = query("SELECT q.*, (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id=q.id) as q_count, (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id=q.id) as attempts FROM quizzes q WHERE q.class_id=? ORDER BY q.created_at DESC", (cid,))
    my_attempts = {}
    if user["role"] == "student":
        atts = query("SELECT * FROM quiz_attempts WHERE student_id=?", (user["id"],))
        my_attempts = {a["quiz_id"]: a for a in atts}
    return render_template("quizzes.html", cls=cls, quizzes=qlist, my_attempts=my_attempts)

@app.route("/classes/<int:cid>/quizzes/create", methods=["POST"])
@login_required
@roles_required("teacher","admin","super_admin")
def create_quiz(cid):
    user  = current_user()
    cls   = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    title = request.form.get("title","").strip()
    if not title:
        flash("Quiz title required.", "error")
        return redirect(url_for("quizzes", cid=cid))
    qid = _uid()
    execute("INSERT INTO quizzes (id,class_id,teacher_id,title,is_open,created_at) VALUES (?,?,?,?,1,?)", (qid, cid, user["id"], title, _now()))
    # Parse questions from form
    i = 1
    while request.form.get(f"q{i}"):
        q = request.form.get(f"q{i}","").strip()
        a = request.form.get(f"q{i}_a","").strip()
        b = request.form.get(f"q{i}_b","").strip()
        c = request.form.get(f"q{i}_c","").strip()
        d = request.form.get(f"q{i}_d","").strip()
        correct = request.form.get(f"q{i}_correct","a").strip().lower()
        if q and a and b:
            execute("INSERT INTO quiz_questions (id,quiz_id,question,option_a,option_b,option_c,option_d,correct,position) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_uid(), qid, q, a, b, c or "", d or "", correct, i))
        i += 1
    flash("Quiz created!", "success")
    return redirect(url_for("quizzes", cid=cid))

@app.route("/quizzes/<qid>/take", methods=["GET","POST"])
@login_required
def take_quiz(qid):
    user = current_user()
    quiz = query("SELECT q.*, c.name as class_name, c.id as class_id FROM quizzes q JOIN classes c ON c.id=q.class_id WHERE q.id=?", (qid,), one=True)
    if not quiz: abort(404)
    already = query("SELECT * FROM quiz_attempts WHERE quiz_id=? AND student_id=?", (qid, user["id"]), one=True)
    if already:
        return redirect(url_for("quiz_results", qid=qid))
    questions = query("SELECT * FROM quiz_questions WHERE quiz_id=? ORDER BY position", (qid,))
    if request.method == "POST":
        import json
        score = 0
        answers = {}
        for q in questions:
            chosen = request.form.get(f"q_{q['id']}", "").lower()
            answers[q["id"]] = chosen
            if chosen == q["correct"].lower():
                score += 1
        execute("INSERT INTO quiz_attempts (id,quiz_id,student_id,score,total,answers,submitted_at) VALUES (?,?,?,?,?,?,?)",
                (_uid(), qid, user["id"], score, len(questions), json.dumps(answers), _now()))
        return redirect(url_for("quiz_results", qid=qid))
    return render_template("take_quiz.html", quiz=quiz, questions=questions)

@app.route("/quizzes/<qid>/results")
@login_required
def quiz_results(qid):
    user    = current_user()
    quiz    = query("SELECT q.*, c.name as class_name, c.id as class_id FROM quizzes q JOIN classes c ON c.id=q.class_id WHERE q.id=?", (qid,), one=True)
    if not quiz: abort(404)
    attempt = query("SELECT * FROM quiz_attempts WHERE quiz_id=? AND student_id=?", (qid, user["id"]), one=True)
    questions = query("SELECT * FROM quiz_questions WHERE quiz_id=? ORDER BY position", (qid,))
    leaderboard = query("""SELECT qa.score, qa.total, u.username FROM quiz_attempts qa JOIN users u ON u.id=qa.student_id WHERE qa.quiz_id=? ORDER BY qa.score DESC LIMIT 20""", (qid,))
    import json
    answers = json.loads(attempt["answers"]) if attempt and attempt["answers"] else {}
    return render_template("quiz_results.html", quiz=quiz, attempt=attempt, questions=questions, leaderboard=leaderboard, answers=answers)


# ═══════════════════════════════════════════════════════════════════════════════
#  ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/classes/<int:cid>/attendance")
@login_required
def attendance(cid):
    user = current_user()
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    sessions = query("""SELECT s.*, (SELECT COUNT(*) FROM attendance_records r WHERE r.session_id=s.id) as present_count FROM attendance_sessions s WHERE s.class_id=? ORDER BY s.created_at DESC""", (cid,))
    active_session = None
    from datetime import datetime, timezone
    now_str = _now()
    for s in sessions:
        if s["open_until"] > now_str:
            active_session = s
            break
    my_records = {}
    if user["role"] == "student":
        recs = query("SELECT session_id FROM attendance_records WHERE student_id=?", (user["id"],))
        my_records = {r["session_id"] for r in recs}
    members_count = query("SELECT COUNT(*) as c FROM class_members WHERE class_id=?", (cid,), one=True)["c"]
    return render_template("attendance.html", cls=cls, sessions=sessions, active_session=active_session, my_records=my_records, members_count=members_count)

@app.route("/classes/<int:cid>/attendance/open", methods=["POST"])
@login_required
@roles_required("teacher","admin","super_admin")
def open_attendance(cid):
    user  = current_user()
    cls   = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    label = request.form.get("label", "Attendance").strip() or "Attendance"
    minutes = int(request.form.get("minutes", 5) or 5)
    from datetime import datetime, timezone, timedelta
    open_until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    execute("INSERT INTO attendance_sessions (id,class_id,teacher_id,label,open_until,created_at) VALUES (?,?,?,?,?,?)",
            (_uid(), cid, user["id"], label, open_until, _now()))
    members = query("SELECT student_id FROM class_members WHERE class_id=?", (cid,))
    for m in members:
        push_notif(m["student_id"], f"Attendance open for {cls['name']} — mark yourself present!", url_for("attendance", cid=cid), "info", user["id"])
    flash(f"Attendance session opened for {minutes} minutes.", "success")
    return redirect(url_for("attendance", cid=cid))

@app.route("/attendance/<sid>/mark", methods=["POST"])
@login_required
def mark_attendance(sid):
    user = current_user()
    session = query("SELECT * FROM attendance_sessions WHERE id=?", (sid,), one=True)
    if not session: abort(404)
    if session["open_until"] < _now():
        flash("This attendance session has closed.", "error")
        return redirect(url_for("attendance", cid=session["class_id"]))
    existing = query("SELECT 1 FROM attendance_records WHERE session_id=? AND student_id=?", (sid, user["id"]), one=True)
    if not existing:
        execute("INSERT INTO attendance_records (id,session_id,student_id,marked_at) VALUES (?,?,?,?)",
                (_uid(), sid, user["id"], _now()))
        flash("Marked present!", "success")
    else:
        flash("Already marked.", "info")
    return redirect(url_for("attendance", cid=session["class_id"]))


# ═══════════════════════════════════════════════════════════════════════════════
#  RESOURCE LIBRARY
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/classes/<int:cid>/resources")
@login_required
def resources(cid):
    cls  = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    res  = query("SELECT r.*, u.username as uploader FROM resources r JOIN users u ON u.id=r.teacher_id WHERE r.class_id=? ORDER BY r.subject_tag, r.created_at DESC", (cid,))
    tags = list({r["subject_tag"] for r in res if r["subject_tag"]})
    return render_template("resources.html", cls=cls, resources=res, tags=tags)

@app.route("/classes/<int:cid>/resources/upload", methods=["POST"])
@login_required
@roles_required("teacher","admin","super_admin")
def upload_resource(cid):
    user  = current_user()
    cls   = query("SELECT * FROM classes WHERE id=?", (cid,), one=True)
    if not cls: abort(404)
    title = request.form.get("title","").strip()
    desc  = request.form.get("description","").strip()
    tag   = request.form.get("subject_tag","").strip()
    if "file" not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for("resources", cid=cid))
    f = request.files["file"]
    if not f.filename:
        flash("No file selected.", "error")
        return redirect(url_for("resources", cid=cid))
    from werkzeug.utils import secure_filename
    file_path, _, _ = save_upload(f)
    file_name = secure_filename(f.filename)
    if not title: title = file_name
    execute("INSERT INTO resources (id,class_id,teacher_id,title,description,file_path,file_name,subject_tag,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (_uid(), cid, user["id"], title, desc, file_path, file_name, tag, _now()))
    flash("Resource uploaded.", "success")
    return redirect(url_for("resources", cid=cid))


# ═══════════════════════════════════════════════════════════════════════════════
#  POLLS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/polls/create", methods=["POST"])
@login_required
def create_poll():
    user = current_user()
    q    = request.form.get("question","").strip()
    a    = request.form.get("opt_a","").strip()
    b    = request.form.get("opt_b","").strip()
    c    = request.form.get("opt_c","").strip()
    d    = request.form.get("opt_d","").strip()
    ends = request.form.get("ends_at","").strip()
    if not q or not a or not b:
        flash("Question and at least 2 options required.", "error")
        return redirect(url_for("feed"))
    execute("INSERT INTO polls (id,author_id,question,opt_a,opt_b,opt_c,opt_d,scope,ends_at,created_at) VALUES (?,?,?,?,?,?,?,'feed',?,?)",
            (_uid(), user["id"], q, a, b, c, d, ends, _now()))
    flash("Poll posted!", "success")
    return redirect(url_for("feed"))

@app.route("/polls/<pid>/vote", methods=["POST"])
@login_required
def vote_poll(pid):
    user   = current_user()
    choice = request.form.get("choice","").strip()
    poll   = query("SELECT * FROM polls WHERE id=?", (pid,), one=True)
    if not poll: abort(404)
    if poll["ends_at"] and poll["ends_at"] < _now():
        flash("This poll has ended.", "error")
        return redirect(url_for("feed"))
    existing = query("SELECT 1 FROM poll_votes WHERE poll_id=? AND user_id=?", (pid, user["id"]), one=True)
    if existing:
        flash("Already voted.", "info")
    elif choice in ("a","b","c","d"):
        execute("INSERT INTO poll_votes (id,poll_id,user_id,choice) VALUES (?,?,?,?)", (_uid(), pid, user["id"], choice))
    return redirect(url_for("feed"))


# ═══════════════════════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/notif-count")
@login_required
def notif_count():
    from flask import jsonify
    user = current_user()
    n = query("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0", (user["id"],), one=True)
    m = query("""SELECT COUNT(*) as c FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE (c.user_a=? OR c.user_b=?) AND m.sender_id!=? AND m.is_read=0""", (user["id"], user["id"], user["id"]), one=True)
    return jsonify({"count": (n["c"] or 0) + (m["c"] or 0)})


@app.route("/events")
@login_required
def events():
    evs = query("SELECT e.*, u.username as author_name FROM events e JOIN users u ON u.id=e.author_id WHERE e.event_date >= TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD') ORDER BY e.event_date ASC LIMIT 50")
    past = query("SELECT e.*, u.username as author_name FROM events e JOIN users u ON u.id=e.author_id WHERE e.event_date < TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD') ORDER BY e.event_date DESC LIMIT 10")
    return render_template("events.html", events=evs, past=past)

@app.route("/events/create", methods=["POST"])
@login_required
@roles_required("teacher","admin","super_admin")
def create_event():
    user  = current_user()
    title = request.form.get("title","").strip()
    desc  = request.form.get("description","").strip()
    date  = request.form.get("event_date","").strip()
    time  = request.form.get("event_time","").strip()
    loc   = request.form.get("location","").strip()
    if not title or not date:
        flash("Title and date required.", "error")
        return redirect(url_for("events"))
    execute("INSERT INTO events (id,author_id,title,description,event_date,event_time,location,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (_uid(), user["id"], title, desc, date, time, loc, _now()))
    flash("Event created.", "success")
    return redirect(url_for("events"))


# ═══════════════════════════════════════════════════════════════════════════════
#  PEER TUTORING
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/tutoring")
@login_required
def tutoring():
    posts = query("SELECT t.*, u.username as author_name FROM tutoring_posts t JOIN users u ON u.id=t.author_id WHERE t.is_resolved=0 ORDER BY t.created_at DESC LIMIT 60")
    return render_template("tutoring.html", posts=posts)

@app.route("/tutoring/post", methods=["POST"])
@login_required
def tutoring_post():
    user     = current_user()
    ptype    = request.form.get("post_type","need")
    subject  = request.form.get("subject","").strip()
    desc     = request.form.get("description","").strip()
    if not subject:
        flash("Subject required.", "error")
        return redirect(url_for("tutoring"))
    execute("INSERT INTO tutoring_posts (id,author_id,post_type,subject,description,created_at) VALUES (?,?,?,?,?,?)",
            (_uid(), user["id"], ptype, subject, desc, _now()))
    flash("Posted to tutoring board.", "success")
    return redirect(url_for("tutoring"))

@app.route("/tutoring/<tid>/resolve", methods=["POST"])
@login_required
def resolve_tutoring(tid):
    user = current_user()
    post = query("SELECT * FROM tutoring_posts WHERE id=?", (tid,), one=True)
    if not post: abort(404)
    if post["author_id"] != user["id"] and user["role"] not in ("admin","super_admin"):
        abort(403)
    execute("UPDATE tutoring_posts SET is_resolved=1 WHERE id=?", (tid,))
    return redirect(url_for("tutoring"))


# ═══════════════════════════════════════════════════════════════════════════════
#  STUDY GROUPS
# ═══════════════════════════════════════════════════════════════════════════════

import secrets as _secrets

@app.route("/groups")
@login_required
def study_groups():
    user   = current_user()
    my_groups = query("""SELECT sg.* FROM study_groups sg JOIN study_group_members sgm ON sgm.group_id=sg.id WHERE sgm.user_id=? ORDER BY sg.created_at DESC""", (user["id"],))
    return render_template("study_groups.html", my_groups=my_groups)

@app.route("/groups/create", methods=["POST"])
@login_required
def create_study_group():
    user = current_user()
    name = request.form.get("name","").strip()
    desc = request.form.get("description","").strip()
    if not name:
        flash("Group name required.", "error")
        return redirect(url_for("study_groups"))
    gid  = _uid()
    code = _secrets.token_urlsafe(6)
    execute("INSERT INTO study_groups (id,name,description,creator_id,invite_code,created_at) VALUES (?,?,?,?,?,?)",
            (gid, name, desc, user["id"], code, _now()))
    execute("INSERT INTO study_group_members (group_id,user_id,joined_at) VALUES (?,?,?)", (gid, user["id"], _now()))
    flash(f"Group created! Invite code: {code}", "success")
    return redirect(url_for("group_detail", gid=gid))

@app.route("/groups/join", methods=["POST"])
@login_required
def join_study_group():
    user = current_user()
    code = request.form.get("invite_code","").strip()
    grp  = query("SELECT * FROM study_groups WHERE invite_code=?", (code,), one=True)
    if not grp:
        flash("Invalid invite code.", "error")
        return redirect(url_for("study_groups"))
    existing = query("SELECT 1 FROM study_group_members WHERE group_id=? AND user_id=?", (grp["id"], user["id"]), one=True)
    if not existing:
        execute("INSERT INTO study_group_members (group_id,user_id,joined_at) VALUES (?,?,?)", (grp["id"], user["id"], _now()))
    return redirect(url_for("group_detail", gid=grp["id"]))

@app.route("/groups/<gid>", methods=["GET","POST"])
@login_required
def group_detail(gid):
    user = current_user()
    grp  = query("SELECT * FROM study_groups WHERE id=?", (gid,), one=True)
    if not grp: abort(404)
    member = query("SELECT 1 FROM study_group_members WHERE group_id=? AND user_id=?", (gid, user["id"]), one=True)
    if not member and user["role"] not in ("admin","super_admin"): abort(403)
    if request.method == "POST":
        content = request.form.get("content","").strip()
        if content:
            execute("INSERT INTO study_group_messages (id,group_id,author_id,content,created_at) VALUES (?,?,?,?,?)",
                    (_uid(), gid, user["id"], content, _now()))
        return redirect(url_for("group_detail", gid=gid))
    msgs    = query("""SELECT m.*, u.username FROM study_group_messages m JOIN users u ON u.id=m.author_id WHERE m.group_id=? ORDER BY m.created_at ASC LIMIT 200""", (gid,))
    members = query("""SELECT u.username, u.role FROM users u JOIN study_group_members sgm ON sgm.user_id=u.id WHERE sgm.group_id=?""", (gid,))
    return render_template("group_detail.html", grp=grp, msgs=msgs, members=members)


# ═══════════════════════════════════════════════════════════════════════════════
#  SHOUTOUTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/shoutouts")
@login_required
def shoutouts():
    user = current_user()
    posts = query("SELECT * FROM shoutouts WHERE is_approved=1 ORDER BY created_at DESC LIMIT 60")
    pending = []
    if user["role"] in ("admin","super_admin"):
        pending = query("SELECT * FROM shoutouts WHERE is_approved=0 ORDER BY created_at DESC")
    return render_template("shoutouts.html", posts=posts, pending=pending)

@app.route("/shoutouts/post", methods=["POST"])
@login_required
def post_shoutout():
    content = request.form.get("content","").strip()
    if not content or len(content) > 300:
        flash("Shoutout must be 1-300 characters.", "error")
        return redirect(url_for("shoutouts"))
    execute("INSERT INTO shoutouts (id,content,is_approved,created_at) VALUES (?,?,0,?)", (_uid(), content, _now()))
    flash("Shoutout submitted — pending approval.", "success")
    return redirect(url_for("shoutouts"))

@app.route("/shoutouts/<sid>/approve", methods=["POST"])
@login_required
@roles_required("admin","super_admin")
def approve_shoutout(sid):
    execute("UPDATE shoutouts SET is_approved=1 WHERE id=?", (sid,))
    return redirect(url_for("shoutouts"))

@app.route("/shoutouts/<sid>/delete", methods=["POST"])
@login_required
@roles_required("admin","super_admin")
def delete_shoutout(sid):
    execute("DELETE FROM shoutouts WHERE id=?", (sid,))
    return redirect(url_for("shoutouts"))

