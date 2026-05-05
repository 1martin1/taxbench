import uuid
import json
import sqlite3
from typing import List, Optional, Generator

from fastapi import FastAPI, HTTPException, Path, Body, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint, conlist, constr

from jinja2 import Environment, select_autoescape

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Provide a SQLite connection per request.
    `check_same_thread=False` allows the connection to be used in the async FastAPI context.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    # recipes table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,  -- JSON-encoded list
            instructions TEXT NOT NULL
        )
        """
    )
    # comments table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    # ratings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- Jinja2 Environment ----------
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

OVERVIEW_TEMPLATE = jinja_env.from_string(
    """
    <!DOCTYPE html>
    <html>
    <head><title>Recipe Overview</title></head>
    <body>
        <h1>Recipe Overview</h1>
        <ul>
        {% for recipe in recipes %}
            <li><a href="/recipes/{{ recipe.id }}">{{ recipe.title }}</a></li>
        {% endfor %}
        </ul>
    </body>
    </html>
    """
)

RECIPE_DETAIL_TEMPLATE = jinja_env.from_string(
    """
    <!DOCTYPE html>
    <html>
    <head><title>{{ recipe.title }}</title></head>
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

        <h2>Average Rating: {{ recipe.avgRating if recipe.avgRating is not none else 'No ratings yet' }}</h2>

        <h2>Comments</h2>
        {% if recipe.comments %}
            <ul>
            {% for c in recipe.comments %}
                <li>{{ c.comment }}</li>
            {% endfor %}
            </ul>
        {% else %}
            <p>No comments yet.</p>
        {% endif %}
    </body>
    </html>
    """
)


# ---------- Pydantic Schemas ----------
class CommentSchema(BaseModel):
    comment: constr(strip_whitespace=True, min_length=1, max_length=500) = Field(
        ..., example="This recipe is amazing!"
    )


class RatingSchema(BaseModel):
    rating: conint(ge=1, le=5) = Field(..., example=5)


class RecipeCreateSchema(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=200) = Field(
        ..., example="Spaghetti Carbonara"
    )
    ingredients: conlist(
        item_type=constr(strip_whitespace=True, min_length=1, max_length=100),
        min_items=1,
        max_items=50,
    ) = Field(
        ...,
        example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
    )
    instructions: constr(strip_whitespace=True, min_length=1, max_length=2000) = Field(
        ..., example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."
    )


class RecipeSchema(BaseModel):
    id: str = Field(..., example="12345")
    title: str = Field(..., example="Spaghetti Carbonara")
    ingredients: List[str] = Field(..., example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"])
    instructions: str = Field(..., example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.")
    comments: List[CommentSchema] = Field(default_factory=list)
    avgRating: Optional[float] = Field(None, example=3.5)


# ---------- Endpoints ----------
@app.get(
    "/recipes",
    response_class=HTMLResponse,
    summary="Get an overview of recipes",
    responses={200: {"description": "Overview of recipes, including just titles and links to the full recipe"}, 500: {"description": "Server error"}},
)
def get_recipes_overview(db: sqlite3.Connection = Depends(get_db)):
    """
    Return a limited overview (most recent 100 recipes) to avoid excessive memory usage.
    """
    cur = db.cursor()
    cur.execute(
        "SELECT id, title FROM recipes ORDER BY rowid DESC LIMIT 100"
    )
    rows = cur.fetchall()
    recipes = [{"id": row["id"], "title": row["title"]} for row in rows]
    html = OVERVIEW_TEMPLATE.render(recipes=recipes)
    return HTMLResponse(content=html)


@app.post(
    "/recipes/upload",
    response_model=RecipeSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new recipe",
    responses={201: {"description": "Recipe created successfully"}, 400: {"description": "Invalid input"}},
)
def upload_recipe(payload: RecipeCreateSchema = Body(...), db: sqlite3.Connection = Depends(get_db)):
    recipe_id = str(uuid.uuid4())
    cur = db.cursor()
    cur.execute(
        "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
        (recipe_id, payload.title, json.dumps(payload.ingredients), payload.instructions),
    )
    db.commit()
    # Return the created recipe (no comments, no ratings yet)
    return RecipeSchema(
        id=recipe_id,
        title=payload.title,
        ingredients=payload.ingredients,
        instructions=payload.instructions,
        comments=[],
        avgRating=None,
    )


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    summary="Get a recipe",
    responses={200: {"description": "Recipe details, ratings, and comments"}, 404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: str = Path(..., description="Recipe ID"), db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipeId,)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recipe not found")

    ingredients = json.loads(row["ingredients"])

    # Fetch comments
    cur.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (recipeId,)
    )
    comment_rows = cur.fetchall()
    comments = [{"comment": cr["comment"]} for cr in comment_rows]

    # Compute average rating
    cur.execute(
        "SELECT AVG(rating) as avg_rating FROM ratings WHERE recipe_id = ?", (recipeId,)
    )
    rating_row = cur.fetchone()
    avg_rating = rating_row["avg_rating"]
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    recipe_data = {
        "id": row["id"],
        "title": row["title"],
        "ingredients": ingredients,
        "instructions": row["instructions"],
        "comments": comments,
        "avgRating": avg_rating,
    }
    html = RECIPE_DETAIL_TEMPLATE.render(recipe=recipe_data)
    return HTMLResponse(content=html)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a recipe",
    responses={201: {"description": "Comment added successfully"}, 400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(
    recipeId: str = Path(..., description="Recipe ID"),
    payload: CommentSchema = Body(...),
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.cursor()
    # Verify recipe exists
    cur.execute("SELECT 1 FROM recipes WHERE id = ?", (recipeId,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Recipe not found")
    cur.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (recipeId, payload.comment),
    )
    db.commit()
    return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    summary="Rate a recipe",
    responses={201: {"description": "Rating added successfully"}, 400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(
    recipeId: str = Path(..., description="Recipe ID"),
    payload: RatingSchema = Body(...),
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.cursor()
    # Verify recipe exists
    cur.execute("SELECT 1 FROM recipes WHERE id = ?", (recipeId,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Recipe not found")
    cur.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (recipeId, payload.rating),
    )
    db.commit()
    return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)