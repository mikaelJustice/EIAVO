#!/usr/bin/env python3
"""
EIA Voice — Database Migration Script
Copies all data from OLD Render PostgreSQL → NEW Supabase PostgreSQL

Usage:
  1. Set OLD_DATABASE_URL to your expired Render DB URL
  2. Set NEW_DATABASE_URL to your new Supabase URL
  3. Run: python migrate_db.py

The script:
  - Copies every table in the correct order (respecting FK constraints)
  - Skips rows that already exist in destination (safe to re-run)
  - Shows progress for each table
"""

import os, sys, time
import psycopg2
import psycopg2.extras

# ── CONFIGURE THESE ──────────────────────────────────────────────────────────
# Your OLD Render database URL (even if expired, it may still accept connections briefly)
# Find it in Render Dashboard → your old PostgreSQL → Connection → External URL
OLD_DATABASE_URL = os.environ.get("OLD_DATABASE_URL", "")

# Your NEW Supabase URL — use the Transaction Pooler URI from Supabase
# Settings → Database → Transaction pooler → URI
# Make sure to replace [YOUR-PASSWORD] with your actual password
NEW_DATABASE_URL = os.environ.get("NEW_DATABASE_URL", "")
# ─────────────────────────────────────────────────────────────────────────────

def fix_url(url):
    if not url:
        return url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if "sslmode" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "sslmode=require"
    return url

def connect(url, label):
    url = fix_url(url)
    print(f"Connecting to {label}...")
    try:
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor,
                                connect_timeout=15)
        conn.autocommit = True
        print(f"  ✓ Connected to {label}")
        return conn
    except Exception as e:
        print(f"  ✗ Failed to connect to {label}: {e}")
        return None

# Tables in FK-safe order (parents before children)
TABLES = [
    "users",
    "posts",
    "comments",
    "reactions",
    "follows",
    "conversations",
    "messages",
    "notifications",
    "classes",
    "class_members",
    "class_posts",
    "class_replies",
    "channels",
    "channel_follows",
    "channel_posts",
    "channel_comments",
    "assignments",
    "submissions",
    "quizzes",
    "quiz_questions",
    "quiz_attempts",
    "attendance_sessions",
    "attendance_records",
    "resources",
    "polls",
    "poll_votes",
    "events",
    "tutoring_posts",
    "study_groups",
    "study_group_members",
    "study_group_messages",
    "shoutouts",
    "yearbook_years",
    "yearbook_entries",
    "calls",
    "call_candidates",
    "statuses",
    "status_views",
    "reels_settings",
    "reels_usage",
    "user_presence",
]

def get_columns(conn, table):
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s AND table_schema = 'public'
        ORDER BY ordinal_position
    """, (table,))
    return [r["column_name"] for r in cur.fetchall()]

def table_exists(conn, table):
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = %s AND table_schema = 'public'
    """, (table,))
    return bool(cur.fetchone())

def get_pk(conn, table):
    cur = conn.cursor()
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
        ORDER BY kcu.ordinal_position
    """, (table,))
    return [r["column_name"] for r in cur.fetchall()]

def migrate_table(old_conn, new_conn, table):
    if not table_exists(old_conn, table):
        print(f"  {table}: not in source, skipping")
        return 0
    if not table_exists(new_conn, table):
        print(f"  {table}: not in destination, skipping (run app once to create tables)")
        return 0

    old_cols = get_columns(old_conn, table)
    new_cols = get_columns(new_conn, table)
    # Use columns that exist in both
    cols = [c for c in old_cols if c in new_cols]
    if not cols:
        print(f"  {table}: no matching columns")
        return 0

    # Fetch all rows from old
    old_cur = old_conn.cursor()
    old_cur.execute(f'SELECT {", ".join(cols)} FROM "{table}"')
    rows = old_cur.fetchall()

    if not rows:
        print(f"  {table}: 0 rows (empty)")
        return 0

    # Get primary key for conflict handling
    pk_cols = get_pk(new_conn, table)
    if not pk_cols:
        pk_cols = [cols[0]]  # fallback to first column

    pk_available = [c for c in pk_cols if c in cols]

    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))

    if pk_available:
        conflict = f'ON CONFLICT ({", ".join(f"""{c}""" for c in pk_available)}) DO NOTHING'
    else:
        conflict = ""

    insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) {conflict}'

    new_cur = new_conn.cursor()
    inserted = 0
    skipped  = 0
    errors   = 0

    for row in rows:
        values = [row[c] for c in cols]
        try:
            new_cur.execute(insert_sql, values)
            inserted += 1
        except psycopg2.errors.UniqueViolation:
            skipped += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"    Warning on row: {e}")

    print(f"  {table}: {inserted} inserted, {skipped} skipped, {errors} errors  (total source rows: {len(rows)})")
    return inserted

def reset_sequences(new_conn):
    """Reset PostgreSQL SERIAL sequences to max(id)+1 after bulk insert."""
    print("\nResetting sequences...")
    cur = new_conn.cursor()
    cur.execute("""
        SELECT sequence_name FROM information_schema.sequences
        WHERE sequence_schema = 'public'
    """)
    sequences = [r["sequence_name"] for r in cur.fetchall()]
    for seq in sequences:
        # Guess the table/column from the sequence name (usually tablename_colname_seq)
        parts = seq.rsplit("_seq", 1)[0].rsplit("_", 1)
        if len(parts) == 2:
            tbl, col = parts
            try:
                cur.execute(f"""
                    SELECT setval('{seq}',
                        COALESCE((SELECT MAX("{col}") FROM "{tbl}"), 1))
                """)
                print(f"  Reset {seq} ✓")
            except Exception as e:
                pass  # table/col might not match — that's fine

def main():
    print("=" * 60)
    print("EIA Voice Database Migration")
    print("=" * 60)

    if not OLD_DATABASE_URL:
        print("\n✗ OLD_DATABASE_URL not set.")
        print("  Set it as an environment variable or edit this file.")
        print("  Example: export OLD_DATABASE_URL='postgresql://...'")
        sys.exit(1)

    if not NEW_DATABASE_URL:
        print("\n✗ NEW_DATABASE_URL not set.")
        print("  Set it as an environment variable or edit this file.")
        sys.exit(1)

    old_conn = connect(OLD_DATABASE_URL, "OLD (Render)")
    if not old_conn:
        print("\n✗ Cannot connect to old database. Check if it's still accessible.")
        print("  Render free DBs can sometimes be accessed briefly even after expiry.")
        sys.exit(1)

    new_conn = connect(NEW_DATABASE_URL, "NEW (Supabase)")
    if not new_conn:
        print("\n✗ Cannot connect to new database.")
        sys.exit(1)

    print(f"\nMigrating {len(TABLES)} tables...\n")
    total = 0
    for table in TABLES:
        try:
            n = migrate_table(old_conn, new_conn, table)
            total += n
        except Exception as e:
            print(f"  {table}: ERROR — {e}")

    reset_sequences(new_conn)

    print(f"\n{'='*60}")
    print(f"Migration complete! {total} total rows copied.")
    print(f"{'='*60}")
    print("\nNext steps:")
    print("1. Update DATABASE_URL on Render to your new Supabase URL")
    print("2. Redeploy your app")
    print("3. Log in and verify your data is there")

if __name__ == "__main__":
    main()
