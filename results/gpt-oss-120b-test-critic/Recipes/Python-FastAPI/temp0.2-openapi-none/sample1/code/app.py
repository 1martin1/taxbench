import uuid
import json
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from jinja2 import Environment, select_autoescape

# Initialize FastAPI with lifespan for startup tasks
def init_db() -> None:
    """Create database tables if they do not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                ingredients TEXT NOT NULL,
                instructions TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


DB_PATH = "db.sqlite3"


def get_db():
    """Provide a thread‑safe SQLite connection per request."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# Jinja2 environment with auto‑escaping enabled for HTML
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

OVERVIEW_TEMPLATE = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head>
    <title>Recipe Overview</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        h2 {color: #333;}
        ul {list-style-type: none; padding: 0;}
        li {margin-bottom: 0.5rem;}
        a {text-decoration: none; color: #0066cc;}
    </style>
</head>
<body>
    <h1>Recipe Overview</h1>

    <h2>Recent Recipes</h2>
    <ul>
    {% for r in recent %}
        <li><a href="/recipes/{{ r.id }}">{{ r.title }}</a></li>
    {% else %}
        <li>No recipes yet.</li>
    {% endfor %}
    </ul>

    <h2>Top Rated Recipes</h2>
    <ul>
    {% for r in top %}
        <li><a href="/recipes/{{ r.id }}">{{ r.title }}</a> (Avg rating: {{ "%.2f"|format(r.avg) }})</li>
    {% else %}
        <li>No rated recipes yet.</li>
    {% endfor %}
    </ul>
</body>
</html>
"""
)

RECIPE_TEMPLATE = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head>
    <title>{{ recipe.title }}</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        h1 {color: #333;}
        .section {margin-top: 1.5rem;}
        ul {list-style-type: disc; margin-left: 2rem;}
        .comment {margin-bottom: 0.5rem; padding: 0.5rem; background:#f9f9f9; border-radius:4px;}
    </style>
</head>
<body>
    <h1>{{ recipe.title }}</h1>

    <div class="section">
        <h2>Ingredients</h2>
        <ul>
        {% for ing in recipe.ingredients %}
            <li>{{ ing }}</li>
        {% endfor %}
        </ul>
    </div>

    <div class="section">
        <h2>Instructions</h2>
        <p>{{ recipe.instructions }}</p>
    </div>

    <div class="section">
        <h2>Average Rating</h2>
        <p>{{ recipe.avgRating if recipe.avgRating is not none else "No ratings yet." }}</p>
    </div>

    <div class="section">
        <h2>Comments</h2>
        {% if recipe.comments %}
            {% for c in recipe.comments %}
                <div class="comment">{{ c.comment }}</div>
            {% endfor %}
        {% else %}
            <p>No comments yet.</p>
        {% endif %}
    </div>
</body>
</html>
"""
)


# ---------- Pydantic models ----------
class RecipeCreate(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str


class CommentCreate(BaseModel):
    comment: str


class RatingCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut]
    avgRating: Optional[float] = None


# ---------- Helper functions ----------
def fetch_recipe(conn: sqlite3.Connection, recipe_id: str) -> Optional[RecipeOut]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipe_id,)
    )
    row = cur.fetchone()
    if not row:
        return None

    # comments
    cur.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (recipe_id,)
    )
    comments = [CommentOut(comment=r["comment"]) for r in cur.fetchall()]

    # average rating
    cur.execute(
        "SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipe_id,)
    )
    avg_row = cur.fetchone()
    avg_rating = round(avg_row["avg"], 2) if avg_row["avg"] is not None else None

    return RecipeOut(
        id=row["id"],
        title=row["title"],
        ingredients=json.loads(row["ingredients"]),
        instructions=row["instructions"],
        comments=comments,
        avgRating=avg_rating,
    )


def get_recent_recipes(conn: sqlite3.Connection, limit: int = 10):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title FROM recipes ORDER BY rowid DESC LIMIT ?", (limit,)
    )
    return [{"id": r["id"], "title": r["title"]} for r in cur.fetchall()]


def get_top_rated_recipes(conn: sqlite3.Connection, limit: int = 10):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.id, r.title, AVG(rt.rating) as avg_rating
        FROM recipes r
        JOIN ratings rt ON r.id = rt.recipe_id
        GROUP BY r.id
        HAVING COUNT(rt.rating) > 0
        ORDER BY avg_rating DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        {"id": r["id"], "title": r["title"], "avg": round(r["avg_rating"], 2)}
        for r in cur.fetchall()
    ]


# ---------- FastAPI application ----------
app = FastAPI(title="Recipe Sharing App API", version="1.0.0")


@app.on_event("startup")
def startup_event():
    # Retained for compatibility; actual DB init is performed in lifespan.
    pass


# Use lifespan to initialise the database without deprecation warnings
@app.middleware("http")
async def db_lifespan_middleware(request, call_next):
    # Ensure DB is initialised once per application start
    if not hasattr(app.state, "db_initialized"):
        init_db()
        app.state.db_initialized = True
    response = await call_next(request)
    return response


# ---------- Endpoints ----------
@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={500: {"description": "Server error"}},
)
def get_overview(db: sqlite3.Connection = Depends(get_db)):
    try:
        recent = get_recent_recipes(db)
        top = get_top_rated_recipes(db)
        html = OVERVIEW_TEMPLATE.render(recent=recent, top=top)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(recipe: RecipeCreate, db: sqlite3.Connection = Depends(get_db)):
    recipe_id = str(uuid.uuid4())
    try:
        db.execute(
            "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
            (
                recipe_id,
                recipe.title,
                json.dumps(recipe.ingredients),
                recipe.instructions,
            ),
        )
        db.commit()
        created = RecipeOut(
            id=recipe_id,
            title=recipe.title,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            comments=[],
            avgRating=None,
        )
        return JSONResponse(content=created.model_dump(), status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/recipes/{recipe_id}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipe_id: str, db: sqlite3.Connection = Depends(get_db)):
    recipe = fetch_recipe(db, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    html = RECIPE_TEMPLATE.render(recipe=recipe.model_dump())
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/recipes/{recipe_id}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(
    recipe_id: str, comment: CommentCreate, db: sqlite3.Connection = Depends(get_db)
):
    cur = db.cursor()
    cur.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        db.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipe_id, comment.comment),
        )
        db.commit()
        return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/recipes/{recipe_id}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(
    recipe_id: str, rating: RatingCreate, db: sqlite3.Connection = Depends(get_db)
):
    cur = db.cursor()
    cur.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        db.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipe_id, rating.rating),
        )
        db.commit()
        return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)