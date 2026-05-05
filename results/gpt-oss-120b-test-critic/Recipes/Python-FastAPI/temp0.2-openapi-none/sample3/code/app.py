import json
import sqlite3
import uuid
import html
from typing import List, Optional, Generator

from fastapi import FastAPI, HTTPException, Request, status, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, conint

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"


def init_db() -> None:
    """Create tables if they do not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                ingredients TEXT NOT NULL,   -- JSON array
                instructions TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
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


init_db()


# ---------- Dependency ----------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a fresh SQLite connection per request."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


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
def get_recipe_by_id(db: sqlite3.Connection, recipe_id: str) -> Optional[sqlite3.Row]:
    cur = db.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    return cur.fetchone()


def get_comments(db: sqlite3.Connection, recipe_id: str) -> List[dict]:
    cur = db.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (recipe_id,)
    )
    return [{"comment": row["comment"]} for row in cur.fetchall()]


def get_average_rating(db: sqlite3.Connection, recipe_id: str) -> Optional[float]:
    cur = db.execute(
        "SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipe_id,)
    )
    row = cur.fetchone()
    return round(row["avg"], 2) if row["avg"] is not None else None


def render_overview_html(recipes: List[sqlite3.Row]) -> str:
    items_html = ""
    for r in recipes:
        title_escaped = html.escape(r["title"])
        items_html += f'<li><a href="/recipes/{r["id"]}">{title_escaped}</a></li>\n'
    html_content = f"""
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
    return html_content


def render_recipe_html(recipe: sqlite3.Row, comments: List[dict], avg_rating: Optional[float]) -> str:
    title_escaped = html.escape(recipe["title"])
    instructions_escaped = html.escape(recipe["instructions"])
    ingredients_list = json.loads(recipe["ingredients"])
    ingredients_html = "".join(f"<li>{html.escape(ing)}</li>" for ing in ingredients_list)
    comments_html = "".join(f"<p>{html.escape(c['comment'])}</p>" for c in comments) or "<p>No comments yet.</p>"
    rating_display = f"{avg_rating:.2f}" if avg_rating is not None else "No ratings yet"
    html_content = f"""
    <html>
        <head><title>{title_escaped}</title></head>
        <body>
            <h1>{title_escaped}</h1>
            <h2>Ingredients</h2>
            <ul>{ingredients_html}</ul>
            <h2>Instructions</h2>
            <p>{instructions_escaped}</p>
            <h2>Average Rating</h2>
            <p>{rating_display}</p>
            <h2>Comments</h2>
            {comments_html}
        </body>
    </html>
    """
    return html_content


# ---------- Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Return a generic 400 response for validation errors as per OpenAPI spec
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


# ---------- API Endpoints ----------
@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={500: {"description": "Server error"}},
)
def get_recipes_overview(db: sqlite3.Connection = Depends(get_db)):
    try:
        cur = db.execute("SELECT id, title FROM recipes ORDER BY title ASC")
        recipes = cur.fetchall()
        html_content = render_overview_html(recipes)
        return HTMLResponse(content=html_content, status_code=200)
    except Exception:
        # Log the exception internally if needed; return generic error
        raise HTTPException(status_code=500, detail="Server error")


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
        response = RecipeOut(
            id=recipe_id,
            title=recipe.title,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            comments=[],
            avgRating=None,
        )
        return response
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: str, db: sqlite3.Connection = Depends(get_db)):
    recipe = get_recipe_by_id(db, recipeId)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    comments = get_comments(db, recipeId)
    avg_rating = get_average_rating(db, recipeId)
    html_content = render_recipe_html(recipe, comments, avg_rating)
    return HTMLResponse(content=html_content, status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_comment(recipeId: str, comment: CommentCreate, db: sqlite3.Connection = Depends(get_db)):
    if not get_recipe_by_id(db, recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        db.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment.comment),
        )
        db.commit()
        return JSONResponse(content={"detail": "Comment added successfully"}, status_code=201)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}, 404: {"description": "Recipe not found"}},
)
def add_rating(recipeId: str, rating: RatingCreate, db: sqlite3.Connection = Depends(get_db)):
    if not get_recipe_by_id(db, recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        db.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, rating.rating),
        )
        db.commit()
        return JSONResponse(content={"detail": "Rating added successfully"}, status_code=201)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)