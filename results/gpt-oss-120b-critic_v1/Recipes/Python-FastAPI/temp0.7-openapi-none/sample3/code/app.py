import uuid
import json
import sqlite3
import threading
import html
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint, conlist, constr

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"
db_lock = threading.Lock()
_connection: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    """Return the global SQLite connection."""
    global _connection
    if _connection is None:
        raise RuntimeError("Database not initialized")
    return _connection


def init_db():
    """Initialize the database and create tables if they do not exist."""
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30,
        isolation_level=None,  # autocommit mode; we will manage transactions manually
    )
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Recipes table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,   -- JSON array
            instructions TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # Comments table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    # Ratings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id TEXT PRIMARY KEY,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            created_at TEXT NOT NULL,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    global _connection
    _connection = conn


def close_db():
    """Close the global SQLite connection."""
    global _connection
    if _connection:
        _connection.close()
        _connection = None


@app.on_event("startup")
def on_startup():
    init_db()


@app.on_event("shutdown")
def on_shutdown():
    close_db()


# ---------- Pydantic Schemas ----------
class RecipeCreate(BaseModel):
    title: constr(min_length=1, max_length=200)
    ingredients: conlist(constr(min_length=1, max_length=100), min_items=1, max_items=50)
    instructions: constr(min_length=1, max_length=2000)


class CommentCreate(BaseModel):
    comment: constr(min_length=1, max_length=500)


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


# ---------- Helper Functions ----------
def fetch_recipe(recipe_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    return cur.fetchone()


def fetch_comments(recipe_id: str) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY created_at ASC",
        (recipe_id,),
    )
    return cur.fetchall()


def fetch_average_rating(recipe_id: str) -> Optional[float]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT AVG(rating) as avg_rating FROM ratings WHERE recipe_id = ?",
        (recipe_id,),
    )
    row = cur.fetchone()
    if row and row["avg_rating"] is not None:
        return round(row["avg_rating"], 2)
    return None


# ---------- Routes ----------
@app.get(
    "/recipes",
    response_class=HTMLResponse,
    summary="Get an overview of recipes",
)
def get_recipes_overview():
    conn = get_db()
    cur = conn.cursor()
    # Recent recipes (last 10)
    cur.execute(
        """
        SELECT id, title FROM recipes
        ORDER BY datetime(created_at) DESC
        LIMIT 10
        """
    )
    recent = cur.fetchall()

    # Top-rated recipes (by avg rating, limit 10)
    cur.execute(
        """
        SELECT r.id, r.title, AVG(rt.rating) as avg_rating
        FROM recipes r
        JOIN ratings rt ON r.id = rt.recipe_id
        GROUP BY r.id
        HAVING COUNT(rt.rating) > 0
        ORDER BY avg_rating DESC
        LIMIT 10
        """
    )
    top = cur.fetchall()

    html_parts = [
        "<html><head><title>Recipe Overview</title></head><body>",
        "<h1>Recent Recipes</h1><ul>",
    ]
    for row in recent:
        title = html.escape(row["title"])
        html_parts.append(
            f'<li><a href="/recipes/{row["id"]}">{title}</a></li>'
        )
    html_parts.append("</ul>")

    html_parts.extend(
        ["<h1>Top Rated Recipes</h1><ul>"]
    )
    for row in top:
        title = html.escape(row["title"])
        avg = round(row["avg_rating"], 2)
        html_parts.append(
            f'<li><a href="/recipes/{row["id"]}">{title} (Avg Rating: {avg})</a></li>'
        )
    html_parts.append("</ul></body></html>")

    return HTMLResponse(content="".join(html_parts))


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new recipe",
)
def upload_recipe(recipe: RecipeCreate):
    recipe_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO recipes (id, title, ingredients, instructions, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                recipe.title,
                json.dumps(recipe.ingredients),
                recipe.instructions,
                now,
            ),
        )
        conn.commit()
    return RecipeOut(
        id=recipe_id,
        title=recipe.title,
        ingredients=recipe.ingredients,
        instructions=recipe.instructions,
        comments=[],
        avgRating=None,
    )


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    summary="Get a recipe",
)
def get_recipe(recipeId: str):
    recipe_row = fetch_recipe(recipeId)
    if not recipe_row:
        raise HTTPException(status_code=404, detail="Recipe not found")

    ingredients = json.loads(recipe_row["ingredients"])
    comments_rows = fetch_comments(recipeId)
    avg_rating = fetch_average_rating(recipeId)

    html_parts = [
        "<html><head><title>Recipe Detail</title></head><body>",
        f"<h1>{html.escape(recipe_row['title'])}</h1>",
        "<h2>Ingredients</h2><ul>",
    ]
    for ing in ingredients:
        html_parts.append(f"<li>{html.escape(ing)}</li>")
    html_parts.append("</ul>")

    html_parts.append("<h2>Instructions</h2>")
    html_parts.append(f"<p>{html.escape(recipe_row['instructions'])}</p>")

    html_parts.append("<h2>Comments</h2><ul>")
    for c in comments_rows:
        html_parts.append(f"<li>{html.escape(c['comment'])}</li>")
    html_parts.append("</ul>")

    html_parts.append("<h2>Average Rating</h2>")
    if avg_rating is not None:
        html_parts.append(f"<p>{avg_rating}</p>")
    else:
        html_parts.append("<p>No ratings yet</p>")

    # Simple forms (optional)
    html_parts.append(f"""
    <h3>Add Comment</h3>
    <form action="/recipes/{recipeId}/comments" method="post" enctype="application/json">
        <textarea name="comment"></textarea><br/>
        <button type="submit">Submit</button>
    </form>
    <h3>Rate Recipe</h3>
    <form action="/recipes/{recipeId}/ratings" method="post" enctype="application/json">
        <input type="number" name="rating" min="1" max="5"/><br/>
        <button type="submit">Rate</button>
    </form>
    """)
    html_parts.append("</body></html>")

    return HTMLResponse(content="".join(html_parts))


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a recipe",
)
def add_comment(recipeId: str, comment: CommentCreate):
    if not fetch_recipe(recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    comment_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO comments (id, recipe_id, comment, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (comment_id, recipeId, comment.comment, now),
        )
        conn.commit()
    return JSONResponse(
        content={"detail": "Comment added successfully"}, status_code=201
    )


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    summary="Rate a recipe",
)
def add_rating(recipeId: str, rating: RatingCreate):
    if not fetch_recipe(recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    rating_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ratings (id, recipe_id, rating, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (rating_id, recipeId, rating.rating, now),
        )
        conn.commit()
    return JSONResponse(
        content={"detail": "Rating added successfully"}, status_code=201
    )


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)