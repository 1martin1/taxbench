import json
import html
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, status, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, conint

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
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
def startup_event():
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
    id: int
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut] = []
    avgRating: Optional[float] = None


# Helper functions
def fetch_recipe(recipe_id: int) -> RecipeOut:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recipe not found")

        ingredients = json.loads(row["ingredients"])

        cur.execute(
            "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (recipe_id,)
        )
        comments_rows = cur.fetchall()
        comments = [CommentOut(comment=r["comment"]) for r in comments_rows]

        cur.execute(
            "SELECT AVG(rating) as avg_rating FROM ratings WHERE recipe_id = ?", (recipe_id,)
        )
        rating_row = cur.fetchone()
        avg_rating = rating_row["avg_rating"]
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


def generate_overview_html(recipes: List[sqlite3.Row]) -> str:
    items_html = ""
    for r in recipes:
        title = html.escape(r["title"])
        items_html += f'<li><a href="/recipes/{r["id"]}">{title}</a></li>\n'
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Recipe Overview</title></head>
    <body>
        <h1>Recipe Overview</h1>
        <ul>
            {items_html}
        </ul>
    </body>
    </html>
    """
    return html_content


def generate_recipe_html(recipe: RecipeOut) -> str:
    title = html.escape(recipe.title)
    ingredients_html = "".join(f"<li>{html.escape(ing)}</li>" for ing in recipe.ingredients)
    comments_html = (
        "".join(f"<li>{html.escape(c.comment)}</li>" for c in recipe.comments)
        or "<li>No comments yet.</li>"
    )
    rating_display = (
        f"{recipe.avgRating:.2f}" if recipe.avgRating is not None else "No ratings yet."
    )
    instructions = html.escape(recipe.instructions)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>{title}</title></head>
    <body>
        <h1>{title}</h1>
        <h2>Ingredients</h2>
        <ul>{ingredients_html}</ul>
        <h2>Instructions</h2>
        <p>{instructions}</p>
        <h2>Average Rating</h2>
        <p>{rating_display}</p>
        <h2>Comments</h2>
        <ul>{comments_html}</ul>
    </body>
    </html>
    """
    return html_content


# Endpoints
@app.get(
    "/recipes",
    response_class=Response,
    responses={
        200: {"content": {"text/html": {}}},
        500: {"description": "Server error"},
    },
)
def get_recipes_overview():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        html_content = generate_overview_html(rows)
        return Response(content=html_content, media_type="text/html")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Recipe created successfully"},
        400: {"description": "Invalid input"},
    },
)
def upload_recipe(recipe: RecipeCreate):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
            (recipe.title, json.dumps(recipe.ingredients), recipe.instructions),
        )
        recipe_id = cur.lastrowid
        conn.commit()
        return RecipeOut(
            id=recipe_id,
            title=recipe.title,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            comments=[],
            avgRating=None,
        )
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()


@app.get(
    "/recipes/{recipeId}",
    response_class=Response,
    responses={
        200: {"content": {"text/html": {}}},
        404: {"description": "Recipe not found"},
    },
)
def get_recipe(recipeId: int = Path(..., description="The ID of the recipe")):
    recipe = fetch_recipe(recipeId)
    html_content = generate_recipe_html(recipe)
    return Response(content=html_content, media_type="text/html")


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Comment added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
def add_comment(
    comment: CommentCreate,
    recipeId: int = Path(..., description="The ID of the recipe"),
):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Recipe not found")
        cur.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment.comment),
        )
        conn.commit()
        return JSONResponse(status_code=201, content={"detail": "Comment added successfully"})
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Rating added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
def add_rating(
    rating: RatingCreate,
    recipeId: int = Path(..., description="The ID of the recipe"),
):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Recipe not found")
        cur.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, rating.rating),
        )
        conn.commit()
        return JSONResponse(status_code=201, content={"detail": "Rating added successfully"})
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)