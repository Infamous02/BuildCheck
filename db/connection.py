"""Postgres connection helper. Reads DATABASE_URL from the environment or a .env file."""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv


def get_conn(url: str | None = None) -> psycopg.Connection:
    load_dotenv()
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set. Add it to .env, e.g.\n"
                         "  DATABASE_URL=postgresql://postgres:password@localhost:5432/buildcheck")
    return psycopg.connect(url)
