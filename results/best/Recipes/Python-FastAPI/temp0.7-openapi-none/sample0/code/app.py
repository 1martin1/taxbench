import json
import sqlite3
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Body, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint
from jinja2 import Environment, select_autoescape

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db() -> sqlite3.Connection:
    """Create a new SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    # Recipes table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL, -- JSON array
            instructions TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Comments table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    # Ratings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- Pydantic models ----------
class RecipeCreate(BaseModel):
    title: str = Field(..., example="Spaghetti Carbonara")
    ingredients: List[str] = Field(..., example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"])
    instructions: str = Field(..., example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.")


class CommentCreate(BaseModel):
    comment: str = Field(..., example="This recipe is amazing!")


class RatingCreate(BaseModel):
    rating: conint(ge=1, le=5) = Field(..., example=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut] = []
    avgRating: Optional[float] = None


# ---------- Jinja2 environment ----------
jinja_env = Environment(autoescape=select_autoescape(['html', 'xml']))

OVERVIEW_TEMPLATE = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head>
    <title>Recipe Overview</title>
</head>
<body>
    <h1>Recent Recipes</h1>
    <ul>
    {% for r in recent %}
        <li><a href="/recipes/{{ r.id }}">{{ r.title }}</a></li>
    {% else %}
        <li>No recent recipes.</li>
    {% endfor %}
    </ul>

    <h1>Top Rated Recipes</h1>
    <ul>
    {% for r in top %}
        <li><a href="/recipes/{{ r.id }}">{{ r.title }}</a> (Avg rating: {{ r.avgRating if r.avgRating is not none else "N/A" }})</li>
    {% else %}
        <li>No top rated recipes.</li>
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
</head>
<body>
    <h1>{{ recipe.title }}</h1>
    <h2>Ingredients</h2>
    <ul>
    {% for ing in recipe.ingredients %}
        <li>{{ ing }}</li>
    {% endfor %}
    </ul>

    <h2>Instructions</h2>
    <p>{{ recipe.instructions }}</p>

    <h2>Average Rating</h2>
    <p>{{ recipe.avgRating if recipe.avgRating is not none else "No ratings yet." }}</p>

    <h2>Comments</h2>
    <ul>
    {% for c in recipe.comments %}
        <li>{{ c.comment }}</li>
    {% else %}
        <li>No comments yet.</li>
    {% endfor %}
    </ul>
</body>
</html>
"""
)


# ---------- Helper functions ----------
def fetch_recipe(recipe_id: str) -> Optional[RecipeOut]:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
        row = cur.fetchone()
        if not row:
            return None

        ingredients = json.loads(row["ingredients"])

        cur.execute(
            "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY created_at ASC",
            (recipe_id,),
        )
        comments = [CommentOut(comment=r["comment"]) for r in cur.fetchall()]

        cur.execute(
            "SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipe_id,)
        )
        avg_row = cur.fetchone()
        avg_rating = avg_row["avg"]
        if avg_rating is not None:
            avg_rating = round(float(avg_rating), 2)

        return RecipeOut(
            id=row["id"],
            title=row["title"],
            ingredients=ingredients,
            instructions=row["instructions"],
            comments=comments,
            avgRating=avg_rating,
        )
    finally:
        conn.close()


def recipe_exists(recipe_id: str) -> bool:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,))
        return cur.fetchone() is not None
    finally:
        conn.close()


# ---------- Endpoints ----------
@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={500: {"description": "Server error"}},
)
def get_overview():
    conn = get_db()
    try:
        cur = conn.cursor()
        # Recent recipes (last 10 by creation)
        cur.execute(
            "SELECT id, title FROM recipes ORDER BY created_at DESC LIMIT 10"
        )
        recent = [{"id": r["id"], "title": r["title"]} for r in cur.fetchall()]

        # Top rated recipes (including those without ratings)
        cur.execute(
            """
            SELECT r.id, r.title, AVG(rt.rating) as avgRating
            FROM recipes r
            LEFT JOIN ratings rt ON r.id = rt.recipe_id
            GROUP BY r.id
            ORDER BY avgRating DESC
            LIMIT 10
            """
        )
        top = []
        for r in cur.fetchall():
            avg = r["avgRating"]
            top.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "avgRating": round(avg, 2) if avg is not None else None,
                }
            )

        html = OVERVIEW_TEMPLATE.render(recent=recent, top=top)
        return HTMLResponse(content=html, status_code=200)
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(recipe: RecipeCreate = Body(...)):
    recipe_id = str(uuid.uuid4())
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
            (
                recipe_id,
                recipe.title,
                json.dumps(recipe.ingredients),
                recipe.instructions,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()

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
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: str = Path(..., description="Recipe ID")):
    recipe = fetch_recipe(recipeId)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    html = RECIPE_TEMPLATE.render(recipe=recipe.dict())
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(
    comment: CommentCreate = Body(...),
    recipeId: str = Path(..., description="Recipe ID"),
):
    if not recipe_exists(recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment.comment),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(
    rating: RatingCreate = Body(...),
    recipeId: str = Path(..., description="Recipe ID"),
):
    if not recipe_exists(recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, rating.rating),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)