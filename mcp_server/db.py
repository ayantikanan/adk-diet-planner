"""Meals database (SQLite): building it from the seed file, and reading meals.

- The schema (mcp_server/data/schema.sql) has CHECK constraints, so the
  database itself rejects bad rows (negative calories, unknown meal types...).
- The meal tool reads through a READ-ONLY connection with parameterised
  queries, so a tool call can never change or damage the data.

Rebuild the database after editing the seed file:
    uv run python -m mcp_server.db --rebuild
"""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from mcp_server.config import SERVER_DIR, TOOLS

_MEALS = TOOLS["get_meal_options"]
DB_PATH = SERVER_DIR / _MEALS["database"]
SCHEMA_PATH = SERVER_DIR / _MEALS["schema_file"]
SEED_PATH = SERVER_DIR / _MEALS["seed_file"]
MAX_OPTIONS = int(_MEALS["max_options"])

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
_COLUMNS = ("name", "meal_type", "calories", "protein_g", "carbs_g", "fat_g", "vegetarian")


def build_database(db_path: Path = DB_PATH, seed_path: Path = SEED_PATH) -> int:
    """(Re)creates the database from the schema and seed file. Returns the meal count.

    Everything happens in one transaction: if any seed row is invalid, nothing
    is written and the error says which meal was rejected.
    """
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    tmp_path = Path(db_path).with_suffix(".building")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        with conn:
            for meal in seed:
                try:
                    conn.execute(
                        f"INSERT INTO meals ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [int(meal[c]) if c == "vegetarian" else meal[c] for c in _COLUMNS],
                    )
                except (sqlite3.IntegrityError, KeyError) as e:
                    raise ValueError(f"Invalid meal in seed file: {meal.get('name', meal)} ({e})") from e
        count = conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0]
    finally:
        conn.close()

    tmp_path.replace(db_path)  # swap in the new database only once it is complete
    return count


def ensure_database() -> None:
    """Builds the database on first run if it doesn't exist yet."""
    if not DB_PATH.exists():
        build_database()


def _read_only() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_meals(meal_type: str, vegetarian: bool = False) -> list[dict]:
    """Returns meals of one type (optionally vegetarian only), lowest calories first."""
    ensure_database()
    query = "SELECT name, calories, protein_g, carbs_g, fat_g, vegetarian FROM meals WHERE meal_type = ?"
    params: list = [meal_type]
    if vegetarian:
        query += " AND vegetarian = 1"
    query += " ORDER BY calories LIMIT ?"
    params.append(MAX_OPTIONS)

    with closing(_read_only()) as conn:  # always close, so the file is never left locked
        rows = conn.execute(query, params).fetchall()
    return [{**dict(row), "vegetarian": bool(row["vegetarian"])} for row in rows]


if __name__ == "__main__":
    if "--rebuild" in sys.argv or not DB_PATH.exists():
        print(f"Built {DB_PATH} with {build_database()} meals.")
    else:
        print(f"{DB_PATH} already exists. Use --rebuild to recreate it from {SEED_PATH.name}.")
