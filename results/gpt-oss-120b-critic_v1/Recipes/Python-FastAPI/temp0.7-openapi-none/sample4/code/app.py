import json
import sqlite3
import html
import time
from pathlib import Path
from typing import List, Optional, Dict

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, conint

# ------------------------------
# Configuration
# ------------------------------

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.sqlite3"

MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
RATE_LIMIT = 60  # requests per minute per IP
RATE_WINDOW = 60  # seconds
COMMENT_FETCH_LIMIT = 100  # max comments returned in HTML view

# In‑memory store for rate limiting
_rate_limiter_store: Dict[str, List[float]] = {}


# ------------------------------
# Database utilities
# ------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    # Ensure directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Recipes table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,   -- JSON array
            instructions TEXT NOT NULL
        )
        """
    )
    # Comments table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    # Ratings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


# ------------------------------
# Pydantic models
# ------------------------------

class RecipeCreate(BaseModel):
    title: str = Field(..., max_length=200)
    ingredients: List[str] = Field(..., min_items=1, max_items=100)
    instructions: str = Field(..., max_length=5000)


class CommentCreate(BaseModel):
    comment: str = Field(..., max_length=500)


class RatingCreate(BaseModel):
    rating: conint(ge=1, le=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut] = Field(default_factory=list)
    avgRating: Optional[float] = None


# ------------------------------
# FastAPI app
# ------------------------------

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")


@app.on_event("startup")
def on_startup():
    init_db()


# ------------------------------
# Middleware
# ------------------------------

@app.middleware("http")
async def body_size_limit_middleware(request: Request, call_next):
    # Enforce maximum body size
    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        raise HTTPException(status_code=413, detail="Request body too large")
    # Re‑inject the body for downstream handlers
    async def receive():
        return {"type": "http.request", "body": body}
    request._receive = receive  # type: ignore
    response = await call_next(request)
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "anonymous"
    now = time.time()
    timestamps = _rate_limiter_store.get(client_ip, [])
    # Remove timestamps older than RATE_WINDOW
    timestamps = [ts for ts in timestamps if now - ts < RATE_WINDOW]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    timestamps.append(now)
    _rate_limiter_store[client_ip] = timestamps
    response = await call_next(request)
    return response


# ------------------------------
# Helper functions
# ------------------------------

def _validate_recipe_id(recipe_id_str: str) -> int:
    if not recipe_id_str.isdigit():
        raise HTTPException(status_code=404, detail="Recipe not found")
    return int(recipe_id_str)


def fetch_recipe(conn: sqlite3.Connection, recipe_id: int) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
    )
    return cur.fetchone()


def fetch_comments(conn: sqlite3.Connection, recipe_id: int, limit: int = COMMENT_FETCH_LIMIT) -> List[dict]:
    cur = conn.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC LIMIT ?",
        (recipe_id, limit),
    )
    return [{"comment": row["comment"]} for row in cur.fetchall()]


def fetch_avg_rating(conn: sqlite3.Connection, recipe_id: int) -> Optional[float]:
    cur = conn.execute(
        "SELECT AVG(rating) as avg_rating FROM ratings WHERE recipe_id = ?", (recipe_id,)
    )
    row = cur.fetchone()
    return round(row["avg_rating"], 2) if row["avg_rating"] is not None else None


# ------------------------------
# Routes
# ------------------------------

@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={500: {"description": "Server error"}},
)
def get_recipes_overview(conn: sqlite3.Connection = Depends(get_db)):
    """
    Retrieve a summary of recent recipes as an HTML page.
    """
    try:
        cur = conn.execute(
            """
            SELECT r.id, r.title,
                (SELECT AVG(rating) FROM ratings WHERE recipe_id = r.id) AS avg_rating
            FROM recipes r
            ORDER BY r.id DESC
            LIMIT 20
            """
        )
        recipes = cur.fetchall()
        html_parts = [
            "<!DOCTYPE html>",
            "<html>",
            "<head><title>Recipe Overview</title></head>",
            "<body>",
            "<h1>Recipe Overview</h1>",
            "<ul>",
        ]
        for rec in recipes:
            title = html.escape(rec["title"])
            rid = rec["id"]
            avg_rating = rec["avg_rating"]
            avg_display = f" - Avg Rating: {round(avg_rating,2)}" if avg_rating is not None else ""
            html_parts.append(
                f'<li><a href="/recipes/{rid}">{title}</a>{avg_display}</li>'
            )
        html_parts.extend(["</ul>", "</body>", "</html>"])
        return HTMLResponse(content="\n".join(html_parts), status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(recipe: RecipeCreate, conn: sqlite3.Connection = Depends(get_db)):
    """
    Upload a new recipe.
    """
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
        (recipe.title, json.dumps(recipe.ingredients), recipe.instructions),
    )
    recipe_id = cur.lastrowid
    conn.commit()
    recipe_out = RecipeOut(
        id=str(recipe_id),
        title=recipe.title,
        ingredients=recipe.ingredients,
        instructions=recipe.instructions,
        comments=[],
        avgRating=None,
    )
    return recipe_out


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: str, conn: sqlite3.Connection = Depends(get_db)):
    """
    Retrieve a recipe by its ID as an HTML page.
    """
    rid_int = _validate_recipe_id(recipeId)
    recipe_row = fetch_recipe(conn, rid_int)
    if not recipe_row:
        raise HTTPException(status_code=404, detail="Recipe not found")

    ingredients = json.loads(recipe_row["ingredients"])
    comments = fetch_comments(conn, rid_int)
    avg_rating = fetch_avg_rating(conn, rid_int)

    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        f"<head><title>{html.escape(recipe_row['title'])}</title></head>",
        "<body>",
        f"<h1>{html.escape(recipe_row['title'])}</h1>",
        "<h2>Ingredients</h2>",
        "<ul>",
    ]
    for ing in ingredients:
        html_parts.append(f"<li>{html.escape(ing)}</li>")
    html_parts.extend([
        "</ul>",
        "<h2>Instructions</h2>",
        f"<p>{html.escape(recipe_row['instructions'])}</p>",
        "<h2>Average Rating</h2>",
        f"<p>{avg_rating if avg_rating is not None else 'No ratings yet'}</p>",
        "<h2>Comments</h2>",
        "<ul>",
    ])
    if comments:
        for c in comments:
            html_parts.append(f"<li>{html.escape(c['comment'])}</li>")
    else:
        html_parts.append("<li>No comments yet.</li>")
    html_parts.extend(["</ul>", "</body>", "</html>"])
    return HTMLResponse(content="\n".join(html_parts), status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(recipeId: str, comment: CommentCreate, conn: sqlite3.Connection = Depends(get_db)):
    """
    Add a comment to a recipe.
    """
    rid_int = _validate_recipe_id(recipeId)
    if not fetch_recipe(conn, rid_int):
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (rid_int, comment.comment),
    )
    conn.commit()
    return Response(status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(recipeId: str, rating: RatingCreate, conn: sqlite3.Connection = Depends(get_db)):
    """
    Add a rating to a recipe.
    """
    rid_int = _validate_recipe_id(recipeId)
    if not fetch_recipe(conn, rid_int):
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (rid_int, rating.rating),
    )
    conn.commit()
    return Response(status_code=201)


# ------------------------------
# Run server
# ------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)