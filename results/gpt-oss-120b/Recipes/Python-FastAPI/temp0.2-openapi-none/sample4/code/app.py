import json
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    # recipes table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL, -- JSON array
            instructions TEXT NOT NULL
        )
        """
    )
    # comments table
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
    # ratings table
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
def on_startup():
    init_db()


# Pydantic models
class RecipeCreate(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str


class CommentCreate(BaseModel):
    comment: str


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


# Dependency to fetch a recipe row
def get_recipe_or_404(recipe_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return row


@app.get("/recipes", response_class=HTMLResponse, responses={500: {"description": "Server error"}})
def get_recipes_overview():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        conn.close()
        html_items = ""
        for r in rows:
            html_items += f'<li><a href="/recipes/{r["id"]}">{r["title"]}</a></li>'
        html = f"""
        <html>
            <head><title>Recipe Overview</title></head>
            <body>
                <h1>Recent Recipes</h1>
                <ul>
                    {html_items}
                </ul>
            </body>
        </html>
        """
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(recipe: RecipeCreate):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
        (recipe.title, json.dumps(recipe.ingredients), recipe.instructions),
    )
    recipe_id = cur.lastrowid
    conn.commit()
    conn.close()
    return RecipeOut(
        id=str(recipe_id),
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
def get_recipe(recipeId: str):
    recipe_row = get_recipe_or_404(recipeId)

    # fetch comments
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (recipeId,)
    )
    comment_rows = cur.fetchall()
    comments_html = "".join(f"<li>{c['comment']}</li>" for c in comment_rows) or "<li>No comments yet.</li>"

    # fetch avg rating
    cur.execute(
        "SELECT AVG(rating) as avg_rating FROM ratings WHERE recipe_id = ?", (recipeId,)
    )
    rating_row = cur.fetchone()
    avg_rating = rating_row["avg_rating"]
    rating_display = f"{avg_rating:.2f}" if avg_rating is not None else "No ratings yet."

    # Build HTML
    ingredients = json.loads(recipe_row["ingredients"])
    ingredients_html = "".join(f"<li>{ing}</li>" for ing in ingredients)

    html = f"""
    <html>
        <head><title>{recipe_row["title"]}</title></head>
        <body>
            <h1>{recipe_row["title"]}</h1>
            <h2>Ingredients</h2>
            <ul>{ingredients_html}</ul>
            <h2>Instructions</h2>
            <p>{recipe_row["instructions"]}</p>
            <h2>Average Rating</h2>
            <p>{rating_display}</p>
            <h2>Comments</h2>
            <ul>{comments_html}</ul>
        </body>
    </html>
    """
    conn.close()
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(recipeId: str, comment: CommentCreate):
    # Ensure recipe exists
    get_recipe_or_404(recipeId)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (recipeId, comment.comment),
    )
    conn.commit()
    conn.close()
    return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(recipeId: str, rating: RatingCreate):
    # Ensure recipe exists
    get_recipe_or_404(recipeId)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (recipeId, rating.rating),
    )
    conn.commit()
    conn.close()
    return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)