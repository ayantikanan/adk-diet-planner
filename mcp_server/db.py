"""Meals database (SQLite): building it from the seed file, and reading meals.

- The schema (mcp_server/data/schema.sql) has CHECK constraints, so the
  database itself rejects bad rows (negative calories, unknown meal types...).
- The meal tool reads through a READ-ONLY connection with parameterised
  queries, so a tool call can never change or damage the data.
- Allergens are stored one row per meal and allergen (meal_allergens), so
  filtering out e.g. nuts and dairy is a single SQL query.

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
ALLERGENS = ("dairy", "eggs", "fish", "shellfish", "gluten", "nuts", "peanuts", "soy", "sesame")
_COLUMNS = ("name", "meal_type", "calories", "protein_g", "carbs_g", "fat_g", "vegetarian", "ingredients")


def _row(meal: dict) -> list:
    values = []
    for c in _COLUMNS:
        if c == "vegetarian":
            values.append(int(meal[c]))
        elif c == "ingredients":
            values.append(", ".join(i.strip().lower() for i in meal.get(c, [])))
        else:
            values.append(meal[c])
    return values


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
                    meal_id = conn.execute(
                        f"INSERT INTO meals ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                        _row(meal),
                    ).lastrowid
                    conn.executemany(
                        "INSERT INTO meal_allergens (meal_id, allergen) VALUES (?, ?)",
                        [(meal_id, a) for a in meal.get("allergens", [])],
                    )
                except (sqlite3.IntegrityError, KeyError) as e:
                    raise ValueError(f"Invalid meal in seed file: {meal.get('name', meal)} ({e})") from e
        count = conn.execute("SELECT COUNT(*) FROM meals").fetchone()[0]
    finally:
        conn.close()

    tmp_path.replace(db_path)  # swap in the new database only once it is complete
    return count


def ensure_database() -> None:
    """Builds the database on first run, and rebuilds it when the schema or seed file changes."""
    newest_source = max(SEED_PATH.stat().st_mtime, SCHEMA_PATH.stat().st_mtime)
    if not DB_PATH.exists() or DB_PATH.stat().st_mtime < newest_source:
        build_database()


def _read_only() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_meals(
    meal_type: str,
    vegetarian: bool = False,
    avoid_allergens: tuple[str, ...] | list[str] = (),
    dislikes: tuple[str, ...] | list[str] = (),
) -> list[dict]:
    """Returns meals of one type, lowest calories first.

    vegetarian:      only vegetarian meals.
    avoid_allergens: leave out meals containing any of these (names from ALLERGENS).
    dislikes:        leave out meals whose name or ingredients contain any of these words.
    Every value is passed as a query parameter, never pasted into the SQL.
    """
    ensure_database()
    query = (
        "SELECT name, calories, protein_g, carbs_g, fat_g, vegetarian, ingredients,"
        " (SELECT group_concat(allergen, ', ') FROM meal_allergens a WHERE a.meal_id = meals.id) AS allergens"
        " FROM meals WHERE meal_type = ?"
    )
    params: list = [meal_type]
    if vegetarian:
        query += " AND vegetarian = 1"
    if avoid_allergens:
        marks = ", ".join("?" * len(avoid_allergens))
        query += f" AND id NOT IN (SELECT meal_id FROM meal_allergens WHERE allergen IN ({marks}))"
        params += list(avoid_allergens)
    for word in dislikes:
        query += " AND instr(lower(name || ', ' || ingredients), ?) = 0"
        params.append(word.lower())
    query += " ORDER BY calories LIMIT ?"
    params.append(MAX_OPTIONS)

    with closing(_read_only()) as conn:  # always close, so the file is never left locked
        rows = conn.execute(query, params).fetchall()
    return [
        {
            **dict(row),
            "vegetarian": bool(row["vegetarian"]),
            "ingredients": [i for i in row["ingredients"].split(", ") if i],
            "allergens": sorted((row["allergens"] or "").split(", ")) if row["allergens"] else [],
        }
        for row in rows
    ]


if __name__ == "__main__":
    if "--rebuild" in sys.argv or not DB_PATH.exists():
        print(f"Built {DB_PATH} with {build_database()} meals.")
    else:
        print(f"{DB_PATH} already exists. Use --rebuild to recreate it from {SEED_PATH.name}.")
