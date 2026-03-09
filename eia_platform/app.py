"""
EIA Voice Platform — Flask + SQLite
A beautiful, Instagram/Facebook-style anonymous school social platform.
Anonymity is guaranteed to users; Super Admin can reveal identities if abuse occurs.
"""

import sqlite3
import hashlib
import secrets
import os
import json
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import (
    Flask, render_template, redirect, url_for,
    request, session, jsonify, flash, send_from_directory, abort, g
)
from werkzeug.utils import secure_filename

# ─── Paths & Config ───────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH    = DATA_DIR / "eia.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    # Images
    "png", "jpg", "jpeg", "gif", "webp",
    # Video
    "mp4", "mov", "webm",
    # Documents
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "txt", "zip", "rar", "7z"
}
MAX_UPLOAD_BYTES   = 30 * 1024 * 1024  # 30 MB

ROLES = ["student", "teacher", "senator", "admin", "super_admin"]

ROLE_RECIPIENTS = {
    "student":     ["all_school"],
    "teacher":     ["all_school", "teachers", "senate", "super_admin"],
    "senator":     ["all_school", "senate", "super_admin"],
    "admin":       ["all_school", "teachers", "senate", "admins", "super_admin"],
    "super_admin": ["all_school", "teachers", "senate", "admins", "super_admin"],
}

RECIPIENT_LABELS = {
    "all_school": "Whole School",
    "teachers":   "Teachers Only",
    "senate":     "Senate",
    "admins":     "Admins",
    "super_admin":"Super Admin",
}

# ─── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key        = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

# ─── DB helpers ───────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()

def query(sql, args=(), one=False):
    db  = get_db()
    cur = db.execute(sql, args)
    rv  = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur

def init_db():
    """Create all tables and seed super-admin account."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS users (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        username    TEXT    UNIQUE NOT NULL COLLATE NOCASE,
        password    TEXT    NOT NULL,
        name        TEXT    NOT NULL,
        role        TEXT    NOT NULL DEFAULT 'student',
        bio         TEXT    DEFAULT '',
        avatar      TEXT    DEFAULT '',
        anon_name   TEXT,
        created_at  TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS posts (
        id          TEXT    PRIMARY KEY,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL,
        media_path  TEXT    DEFAULT '',
        media_type  TEXT    DEFAULT '',
        is_anon     INTEGER NOT NULL DEFAULT 0,
        recipient   TEXT    NOT NULL DEFAULT 'all_school',
        flagged     INTEGER NOT NULL DEFAULT 0,
        flag_reason TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS comments (
        id          TEXT    PRIMARY KEY,
        post_id     TEXT    NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content     TEXT    NOT NULL,
        is_anon     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS reactions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id     TEXT    REFERENCES posts(id)    ON DELETE CASCADE,
        comment_id  TEXT    REFERENCES comments(id) ON DELETE CASCADE,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji       TEXT    NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_react_post ON reactions(post_id,    user_id, emoji);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_react_cmt  ON reactions(comment_id, user_id, emoji);

    CREATE TABLE IF NOT EXISTS follows (
        follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL,
        PRIMARY KEY (follower_id, followee_id)
    );

    CREATE TABLE IF NOT EXISTS conversations (
        id          TEXT    PRIMARY KEY,
        user_a      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        user_b      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_conv ON conversations(
        MIN(user_a,user_b), MAX(user_a,user_b)
    );

    CREATE TABLE IF NOT EXISTS messages (
        id              TEXT    PRIMARY KEY,
        conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        sender_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content         TEXT    NOT NULL,
        is_anon         INTEGER NOT NULL DEFAULT 0,
        is_read         INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text        TEXT    NOT NULL,
        link        TEXT    DEFAULT '',
        notif_type  TEXT    DEFAULT 'info',
        is_read     INTEGER NOT NULL DEFAULT 0,
        actor_id    INTEGER,
        created_at  TEXT    NOT NULL
    );
    """)

    # Seed super admin
    existing = db.execute("SELECT id FROM users WHERE username='superadmin'").fetchone()
    if not existing:
        pw   = hashlib.sha256("SuperAdmin@EIA2024!".encode()).hexdigest()
        anon = f"Shadow_{secrets.token_hex(3).upper()}"
        db.execute(
            "INSERT INTO users (username,password,name,role,anon_name,created_at) VALUES (?,?,?,?,?,?)",
            ("superadmin", pw, "Super Administrator", "super_admin", anon, _now())
        )
    db.commit()
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

def save_upload(file) -> tuple[str, str]:
    """Save uploaded file, return (filename, media_type)."""
    if not file or not file.filename:
        return "", ""
    if not _allowed(file.filename):
        return "", ""
    ext  = file.filename.rsplit(".", 1)[1].lower()
    name = f"{_uid()}.{ext}"
    file.save(str(UPLOAD_DIR / name))
    
    # Determine media type
    if _is_video(name):
        mtype = "video"
    elif ext in {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "zip", "rar", "7z"}:
        mtype = "document"
    else:  # images
        mtype = "image"
    
    return name, mtype

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
        _fmt=_fmt_time, _rel=_relative_time,
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
        display    = author["name"] if author else "Unknown"
        avatar     = author["avatar"] if author else ""
        role_shown = author["role"]   if author else ""

    # Reactions
    raw_reacts = query(
        "SELECT emoji, COUNT(*) as c, GROUP_CONCAT(user_id) as uids FROM reactions WHERE post_id=? GROUP BY emoji",
        (row["id"],)
    )
    reactions = {}
    for r in raw_reacts:
        uids = [int(x) for x in (r["uids"] or "").split(",") if x]
        reactions[r["emoji"]] = {"count": r["c"], "liked": (viewer_id in uids) if viewer_id else False}

    # Comments
    raw_cmts = query(
        """SELECT c.*, u.name, u.role, u.anon_name, u.avatar, u.username
           FROM comments c JOIN users u ON c.author_id=u.id
           WHERE c.post_id=? ORDER BY c.created_at ASC""",
        (row["id"],)
    )
    comments = []
    for c in raw_cmts:
        ca_display = c["anon_name"] if (c["is_anon"] and c["anon_name"]) else c["name"]
        ca_avatar  = "" if c["is_anon"] else c["avatar"]
        comments.append({
            "id": c["id"], "content": c["content"],
            "display": ca_display, "avatar": ca_avatar,
            "role": c["role"], "username": c["username"],
            "is_anon": bool(c["is_anon"]),
            "created_at": c["created_at"],
            "author_id": c["author_id"],
            "relative": _relative_time(c["created_at"]),
        })

    return {
        "id": row["id"], "content": row["content"],
        "media": row["media_path"], "media_type": row["media_type"],
        "is_anon": is_anon, "display": display, "avatar": avatar,
        "role": role_shown, "author_id": row["author_id"],
        "recipient": row["recipient"], "flagged": bool(row["flagged"]),
        "flag_reason": row["flag_reason"], "reactions": reactions,
        "comments": comments, "comment_count": len(comments),
        "created_at": row["created_at"], "relative": _relative_time(row["created_at"]),
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

    visible = {
        "super_admin": [],
        "admin":       ["all_school","admins"],
        "teacher":     ["all_school","teachers"],
        "senator":     ["all_school","senate"],
        "student":     ["all_school"],
    }.get(role, ["all_school"])

    if role == "super_admin":
        rows = query("SELECT * FROM posts ORDER BY created_at DESC LIMIT 100")
    else:
        placeholders = ",".join("?" * len(visible))
        rows = query(
            f"SELECT * FROM posts WHERE recipient IN ({placeholders}) ORDER BY created_at DESC LIMIT 100",
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
    if not content:
        flash("Post cannot be empty.", "error")
        return redirect(url_for("feed"))

    is_anon   = bool(request.form.get("is_anon"))
    recipient = request.form.get("recipient","all_school")
    role      = user["role"]

    # Students always post to whole school
    if role == "student":
        recipient = "all_school"

    # Validate recipient for role
    allowed_recipients = ROLE_RECIPIENTS.get(role, ["all_school"])
    if recipient not in allowed_recipients:
        recipient = "all_school"

    media_path, media_type = "", ""
    if "media" in request.files:
        f = request.files["media"]
        if f and f.filename:
            media_path, media_type = save_upload(f)

    pid = _uid()
    execute(
        "INSERT INTO posts (id,author_id,content,media_path,media_type,is_anon,recipient,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (pid, user["id"], content, media_path, media_type, 1 if is_anon else 0, recipient, _now())
    )

    # Notify followers
    display = get_anon_name(user["id"]) if is_anon else user["name"]
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
        display = get_anon_name(user["id"]) if is_anon else user["name"]
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
    post_rows = query("SELECT * FROM posts WHERE author_id=? ORDER BY created_at DESC", (prof["id"],))
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
        name     = request.form.get("name","").strip() or user["name"]
        bio      = request.form.get("bio","").strip()
        avatar   = user["avatar"]
        anon_nm  = request.form.get("anon_name","").strip()

        if "avatar" in request.files:
            f = request.files["avatar"]
            if f and f.filename:
                fname, _ = save_upload(f)
                if fname:
                    avatar = fname

        if anon_nm:
            anon_nm = anon_nm[:20]
        else:
            anon_nm = user["anon_name"] or get_anon_name(user["id"])

        execute("UPDATE users SET name=?,bio=?,avatar=?,anon_name=? WHERE id=?",
                (name, bio, avatar, anon_nm, user["id"]))
        session["name"] = name
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
@app.route("/people")
@login_required
def people():
    viewer = current_user()
    search = request.args.get("q","").strip()
    if search:
        users = query(
            """SELECT u.*,
               (SELECT COUNT(*) FROM follows WHERE followee_id=u.id) as followers,
               (SELECT COUNT(*) FROM follows WHERE follower_id=u.id) as following_ct,
               (SELECT 1     FROM follows WHERE follower_id=? AND followee_id=u.id) as i_follow
               FROM users u WHERE u.id!=? AND (LOWER(u.name) LIKE ? OR LOWER(u.username) LIKE ?)
               ORDER BY u.name""",
            (viewer["id"], viewer["id"], f"%{search.lower()}%", f"%{search.lower()}%")
        )
    else:
        users = query(
            """SELECT u.*,
               (SELECT COUNT(*) FROM follows WHERE followee_id=u.id) as followers,
               (SELECT COUNT(*) FROM follows WHERE follower_id=u.id) as following_ct,
               (SELECT 1     FROM follows WHERE follower_id=? AND followee_id=u.id) as i_follow
               FROM users u WHERE u.id!=? ORDER BY u.name""",
            (viewer["id"], viewer["id"])
        )
    return render_template("people.html", users=users, search=search)

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
           CASE WHEN c.user_a=? THEN ub.name   ELSE ua.name   END as other_name,
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
        content = request.form.get("content","").strip()
        is_anon = bool(request.form.get("is_anon"))
        if content:
            execute(
                "INSERT INTO messages (id,conversation_id,sender_id,content,is_anon,created_at) VALUES (?,?,?,?,?,?)",
                (_uid(), conv_id, user["id"], content, 1 if is_anon else 0, _now())
            )
            display = get_anon_name(user["id"]) if is_anon else user["name"]
            push_notif(other_id, f"New message from {display}",
                       url_for("conversation", conv_id=conv_id), "message")
        return redirect(url_for("conversation", conv_id=conv_id))

    msgs = query(
        """SELECT m.*, u.name, u.avatar, u.anon_name FROM messages m
           JOIN users u ON m.sender_id=u.id
           WHERE m.conversation_id=? ORDER BY m.created_at ASC""",
        (conv_id,)
    )
    # Mark as read
    execute("UPDATE messages SET is_read=1 WHERE conversation_id=? AND sender_id!=?",
            (conv_id, user["id"]))
    return render_template("conversation.html", conv=conv, other=other, msgs=msgs)

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
            if user["password"] != _hash(old_pw):
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
        """SELECT p.*, u.name as uname, u.username as uusername
           FROM posts p JOIN users u ON p.author_id=u.id
           WHERE p.flagged=1 ORDER BY p.created_at DESC"""
    )
    stats = {
        "users":    query("SELECT COUNT(*) as c FROM users",             one=True)["c"],
        "posts":    query("SELECT COUNT(*) as c FROM posts",             one=True)["c"],
        "flagged":  query("SELECT COUNT(*) as c FROM posts WHERE flagged=1", one=True)["c"],
        "anon":     query("SELECT COUNT(*) as c FROM posts WHERE is_anon=1", one=True)["c"],
        "messages": query("SELECT COUNT(*) as c FROM messages",          one=True)["c"],
        "follows":  query("SELECT COUNT(*) as c FROM follows",           one=True)["c"],
    }
    return render_template("admin.html", users=users, flagged=flagged, stats=stats,
                           ROLES=ROLES, ROLE_RECIPIENTS=ROLE_RECIPIENTS)

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
    try:
        execute(
            "INSERT INTO users (username,password,name,role,anon_name,created_at) VALUES (?,?,?,?,?,?)",
            (uname, _hash(pw), name, role, anon, _now())
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

# ─── Media serving ────────────────────────────────────────────────────────────
@app.route("/media/<filename>")
def serve_media(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)

# ─── Error handlers ───────────────────────────────────────────────────────────
@app.errorhandler(403)
def e403(e): return render_template("error.html", code=403, msg="Access Forbidden"), 403

@app.errorhandler(404)
def e404(e): return render_template("error.html", code=404, msg="Page Not Found"), 404

# ─── Entrypoint ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
