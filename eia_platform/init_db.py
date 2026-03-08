"""Initialise the database — run once before starting the server."""
from app import init_db
init_db()
print("✓ Database initialised successfully.")
