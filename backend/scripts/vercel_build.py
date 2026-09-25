"""Vercel build step: apply migrations when a database is attached; skip (not fail) otherwise."""
import os
import subprocess
import sys

NAMES = ("DATABASE_URL", "POSTGRES_URL", "DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING")

if not any(os.environ.get(n) for n in NAMES):
    print("vercel_build: no database attached yet; skipping migrations")
    sys.exit(0)
# Prefer the direct (non-pooled) connection for DDL when the integration provides one.
direct = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("POSTGRES_URL_NON_POOLING")
env = {**os.environ, **({"DATABASE_URL": direct} if direct else {})}
sys.exit(subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"], env=env))
