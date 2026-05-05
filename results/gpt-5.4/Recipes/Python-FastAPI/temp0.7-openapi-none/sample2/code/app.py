import html
import sqlite3
import uuid
from contextlib import closing
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                ingredient TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipe_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipe_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


class RecipeUploadRequest(BaseModel):
    title: str = Field(..., min_length=1)
    ingredients: List[str] = Field(..., min_length=1)
    instructions: str = Field(..., min_length=1)


class CommentRequest(BaseModel):
    comment: str = Field(..., min_length=1)


class RatingRequest(BaseModel):
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


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def get_recipe_data(conn: sqlite3.Connection, recipe_id: str) -> Optional[dict]:
    recipe_row = conn.execute(
        "SELECT id, title, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe_row is None:
        return None

    ingredient_rows = conn.execute(
        "SELECT ingredient FROM recipe_ingredients WHERE recipe_id = ? ORDER BY id ASC",
        (recipe_id,),
    ).fetchall()

    comment_rows = conn.execute(
        "SELECT comment FROM recipe_comments WHERE recipe_id = ? ORDER BY id ASC",
        (recipe_id,),
    ).fetchall()

    rating_row = conn.execute(
        "SELECT AVG(rating) AS avg_rating FROM recipe_ratings WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()

    avg_rating = rating_row["avg_rating"]
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    return {
        "id": recipe_row["id"],
        "title": recipe_row["title"],
        "ingredients": [row["ingredient"] for row in ingredient_rows],
        "instructions": recipe_row["instructions"],
        "comments": [{"comment": row["comment"]} for row in comment_rows],
        "avgRating": avg_rating,
    }


@app.get("/recipes", response_class=HTMLResponse, responses={500: {"description": "Server error"}})
def get_recipes_overview() -> HTMLResponse:
    try:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    COALESCE(AVG(rr.rating), 0) AS avg_rating,
                    COUNT(rr.id) AS rating_count
                FROM recipes r
                LEFT JOIN recipe_ratings rr ON r.id = rr.recipe_id
                GROUP BY r.id, r.title
                ORDER BY avg_rating DESC, rating_count DESC, r.rowid DESC
                """
            ).fetchall()

        items = []
        for row in rows:
            title = html.escape(row["title"])
            recipe_id = html.escape(row["id"])
            items.append(f'<li><a href="/recipes/{recipe_id}">{title}</a></li>')

        body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Recipe Overview</title>
        </head>
        <body>
            <h1>Recipe Overview</h1>
            <ul>
                {''.join(items) if items else '<li>No recipes available.</li>'}
            </ul>
        </body>
        </html>
        """
        return HTMLResponse(content=body, status_code=200)
    except Exception:
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"><title>Server Error</title></head>
            <body><h1>Server Error</h1></body>
            </html>
            """,
            status_code=500,
        )


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=201,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(payload: RecipeUploadRequest) -> RecipeOut:
    title = payload.title.strip()
    instructions = payload.instructions.strip()
    ingredients = [item.strip() for item in payload.ingredients]

    if not title or not instructions or not ingredients or any(not item for item in ingredients):
        raise HTTPException(status_code=400, detail="Invalid input")

    recipe_id = str(uuid.uuid4())

    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, instructions) VALUES (?, ?, ?)",
            (recipe_id, title, instructions),
        )
        conn.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient) VALUES (?, ?)",
            [(recipe_id, ingredient) for ingredient in ingredients],
        )
        conn.commit()

        recipe = get_recipe_data(conn, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=500, detail="Failed to create recipe")

    return RecipeOut(**recipe)


@app.get("/recipes/{recipeId}", response_class=HTMLResponse, responses={404: {"description": "Recipe not found"}})
def get_recipe(recipeId: str) -> HTMLResponse:
    with closing(get_connection()) as conn:
        recipe = get_recipe_data(conn, recipeId)

    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    ingredients_html = "".join(f"<li>{html.escape(item)}</li>" for item in recipe["ingredients"])
    comments_html = "".join(
        f"<li>{html.escape(comment['comment'])}</li>" for comment in recipe["comments"]
    )

    avg_rating_display = "No ratings yet" if recipe["avgRating"] is None else str(recipe["avgRating"])

    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{html.escape(recipe["title"])}</title>
    </head>
    <body>
        <h1>{html.escape(recipe["title"])}</h1>
        <p><strong>ID:</strong> {html.escape(recipe["id"])}</p>
        <h2>Ingredients</h2>
        <ul>
            {ingredients_html}
        </ul>
        <h2>Instructions</h2>
        <p>{html.escape(recipe["instructions"])}</p>
        <h2>Average Rating</h2>
        <p>{html.escape(avg_rating_display)}</p>
        <h2>Comments</h2>
        <ul>
            {comments_html if comments_html else '<li>No comments yet.</li>'}
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=body, status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=201,
    responses={
        201: {"description": "Comment added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
def add_comment(recipeId: str, payload: CommentRequest) -> Response:
    comment = payload.comment.strip()
    if not comment:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_connection()) as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO recipe_comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment),
        )
        conn.commit()

    return JSONResponse(status_code=201, content={"message": "Comment added successfully"})


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=201,
    responses={
        201: {"description": "Rating added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
def add_rating(recipeId: str, payload: RatingRequest) -> Response:
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_connection()) as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO recipe_ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )
        conn.commit()

    return JSONResponse(status_code=201, content={"message": "Rating added successfully"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)