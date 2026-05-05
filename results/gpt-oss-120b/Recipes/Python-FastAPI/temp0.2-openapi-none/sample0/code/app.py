import json
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Body, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint

from jinja2 import Template

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,  -- JSON array
            instructions TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
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
    id: int
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut] = []
    avgRating: Optional[float] = None


# ---------- HTML templates ----------
OVERVIEW_TEMPLATE = Template(
    """
<!DOCTYPE html>
<html>
<head>
    <title>Recipe Overview</title>
</head>
<body>
    <h1>Recent Recipes</h1>
    <ul>
    {% for recipe in recipes %}
        <li><a href="/recipes/{{ recipe.id }}">{{ recipe.title }}</a></li>
    {% else %}
        <li>No recipes found.</li>
    {% endfor %}
    </ul>
</body>
</html>
"""
)

RECIPE_DETAIL_TEMPLATE = Template(
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
def fetch_recipe(recipe_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    # ingredients stored as JSON
    ingredients = json.loads(row["ingredients"])
    # fetch comments
    cur.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipe_id,))
    comments = [{"comment": r["comment"]} for r in cur.fetchall()]
    # fetch avg rating
    cur.execute("SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipe_id,))
    avg_row = cur.fetchone()
    avg_rating = round(avg_row["avg"], 2) if avg_row["avg"] is not None else None
    conn.close()
    return {
        "id": row["id"],
        "title": row["title"],
        "ingredients": ingredients,
        "instructions": row["instructions"],
        "comments": comments,
        "avgRating": avg_rating,
    }


# ---------- Endpoints ----------
@app.get("/recipes", response_class=HTMLResponse, responses={500: {"description": "Server error"}})
def get_recipes_overview():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 20")
        recipes = [{"id": r["id"], "title": r["title"]} for r in cur.fetchall()]
        conn.close()
        html = OVERVIEW_TEMPLATE.render(recipes=recipes)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=201,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(recipe: RecipeCreate = Body(...)):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
            (recipe.title, json.dumps(recipe.ingredients), recipe.instructions),
        )
        recipe_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    # Build response
    response = RecipeOut(
        id=recipe_id,
        title=recipe.title,
        ingredients=recipe.ingredients,
        instructions=recipe.instructions,
        comments=[],
        avgRating=None,
    )
    return response


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: int = Path(..., description="The ID of the recipe")):
    data = fetch_recipe(recipeId)
    html = RECIPE_DETAIL_TEMPLATE.render(recipe=data)
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=201,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(
    recipeId: int = Path(..., description="The ID of the recipe"),
    comment: CommentCreate = Body(...),
):
    # Ensure recipe exists
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        cur.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment.comment),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=201,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(
    recipeId: int = Path(..., description="The ID of the recipe"),
    rating: RatingCreate = Body(...),
):
    # Ensure recipe exists
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        cur.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, rating.rating),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)