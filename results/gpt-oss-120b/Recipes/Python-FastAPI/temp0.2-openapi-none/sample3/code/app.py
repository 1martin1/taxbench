import json
import sqlite3
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute(
    """
CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    ingredients TEXT NOT NULL,   -- JSON array
    instructions TEXT NOT NULL
)
"""
)

cursor.execute(
    """
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id TEXT NOT NULL,
    comment TEXT NOT NULL,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
)
"""
)

cursor.execute(
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


# ---------- Pydantic Schemas ----------
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


# ---------- Helper Functions ----------
def get_recipe_by_id(recipe_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    return cur.fetchone()


def get_comments(recipe_id: str) -> List[dict]:
    cur = conn.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (recipe_id,)
    )
    return [{"comment": row["comment"]} for row in cur.fetchall()]


def get_average_rating(recipe_id: str) -> Optional[float]:
    cur = conn.execute(
        "SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipe_id,)
    )
    row = cur.fetchone()
    return round(row["avg"], 2) if row["avg"] is not None else None


def render_overview_html(recipes: List[sqlite3.Row]) -> str:
    items_html = ""
    for r in recipes:
        items_html += f'<li><a href="/recipes/{r["id"]}">{r["title"]}</a></li>\n'
    html = f"""
    <html>
        <head><title>Recipe Overview</title></head>
        <body>
            <h1>Recipes</h1>
            <ul>
                {items_html}
            </ul>
        </body>
    </html>
    """
    return html


def render_recipe_html(recipe: sqlite3.Row, comments: List[dict], avg_rating: Optional[float]) -> str:
    ingredients_html = "".join(f"<li>{ing}</li>" for ing in json.loads(recipe["ingredients"]))
    comments_html = "".join(f"<p>{c['comment']}</p>" for c in comments) or "<p>No comments yet.</p>"
    rating_display = f"{avg_rating:.2f}" if avg_rating is not None else "No ratings yet"
    html = f"""
    <html>
        <head><title>{recipe["title"]}</title></head>
        <body>
            <h1>{recipe["title"]}</h1>
            <h2>Ingredients</h2>
            <ul>{ingredients_html}</ul>
            <h2>Instructions</h2>
            <p>{recipe["instructions"]}</p>
            <h2>Average Rating</h2>
            <p>{rating_display}</p>
            <h2>Comments</h2>
            {comments_html}
        </body>
    </html>
    """
    return html


# ---------- API Endpoints ----------
@app.get("/recipes", response_class=HTMLResponse, responses={500: {"description": "Server error"}})
def get_recipes_overview():
    try:
        cur = conn.execute("SELECT id, title FROM recipes ORDER BY title ASC")
        recipes = cur.fetchall()
        html = render_overview_html(recipes)
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
    recipe_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
            (
                recipe_id,
                recipe.title,
                json.dumps(recipe.ingredients),
                recipe.instructions,
            ),
        )
        conn.commit()
        # Build response object
        response = RecipeOut(
            id=recipe_id,
            title=recipe.title,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            comments=[],
            avgRating=None,
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: str):
    recipe = get_recipe_by_id(recipeId)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    comments = get_comments(recipeId)
    avg_rating = get_average_rating(recipeId)
    html = render_recipe_html(recipe, comments, avg_rating)
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(recipeId: str, comment: CommentCreate):
    if not get_recipe_by_id(recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment.comment),
        )
        conn.commit()
        return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(recipeId: str, rating: RatingCreate):
    if not get_recipe_by_id(recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, rating.rating),
        )
        conn.commit()
        return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)