import html
import sqlite3
import uuid
from contextlib import contextmanager
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
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
            CREATE TABLE IF NOT EXISTS ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                ingredient TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
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
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )


class RecipeUploadRequest(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str


class CommentRequest(BaseModel):
    comment: str


class RatingRequest(BaseModel):
    rating: int = Field(ge=1, le=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut]
    avgRating: Optional[float]


def validate_recipe_input(payload: RecipeUploadRequest) -> None:
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title must not be empty")
    if not payload.instructions.strip():
        raise HTTPException(status_code=400, detail="Instructions must not be empty")
    if not payload.ingredients:
        raise HTTPException(status_code=400, detail="Ingredients must not be empty")
    if any(not ingredient.strip() for ingredient in payload.ingredients):
        raise HTTPException(status_code=400, detail="Ingredients must not contain empty values")


def validate_comment_input(payload: CommentRequest) -> None:
    if not payload.comment.strip():
        raise HTTPException(status_code=400, detail="Comment must not be empty")


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def get_recipe_data(conn: sqlite3.Connection, recipe_id: str) -> Optional[dict]:
    recipe_row = conn.execute(
        "SELECT id, title, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe_row is None:
        return None

    ingredient_rows = conn.execute(
        "SELECT ingredient FROM ingredients WHERE recipe_id = ? ORDER BY id ASC",
        (recipe_id,),
    ).fetchall()
    comment_rows = conn.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC",
        (recipe_id,),
    ).fetchall()
    avg_row = conn.execute(
        "SELECT AVG(rating) AS avg_rating FROM ratings WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()

    avg_rating = avg_row["avg_rating"]
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


def render_recipes_overview_html(recipes: List[sqlite3.Row]) -> str:
    items = []
    for recipe in recipes:
        title = html.escape(recipe["title"])
        recipe_id = html.escape(recipe["id"])
        avg_rating = recipe["avg_rating"]
        avg_rating_text = "No ratings yet" if avg_rating is None else f"Average rating: {float(avg_rating):.2f}"
        items.append(
            f"""
            <li>
                <a href="/recipes/{recipe_id}">{title}</a>
                <div>{html.escape(avg_rating_text)}</div>
            </li>
            """
        )

    recipe_list = "\n".join(items) if items else "<li>No recipes available.</li>"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Recipe Overview</title>
    </head>
    <body>
        <h1>Recipe Overview</h1>
        <ul>
            {recipe_list}
        </ul>
    </body>
    </html>
    """


def render_recipe_detail_html(recipe: dict) -> str:
    ingredients_html = "".join(
        f"<li>{html.escape(ingredient)}</li>" for ingredient in recipe["ingredients"]
    )
    comments_html = "".join(
        f"<li>{html.escape(comment['comment'])}</li>" for comment in recipe["comments"]
    )
    if not comments_html:
        comments_html = "<li>No comments yet.</li>"

    avg_rating = recipe["avgRating"]
    avg_rating_text = "No ratings yet" if avg_rating is None else f"{avg_rating:.2f}"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>{html.escape(recipe["title"])}</title>
    </head>
    <body>
        <h1>{html.escape(recipe["title"])}</h1>
        <p><strong>ID:</strong> {html.escape(recipe["id"])}</p>
        <p><strong>Average Rating:</strong> {html.escape(avg_rating_text)}</p>

        <h2>Ingredients</h2>
        <ul>
            {ingredients_html}
        </ul>

        <h2>Instructions</h2>
        <p>{html.escape(recipe["instructions"])}</p>

        <h2>Comments</h2>
        <ul>
            {comments_html}
        </ul>
    </body>
    </html>
    """


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/recipes", response_class=HTMLResponse)
def get_recipes_overview():
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    AVG(rt.rating) AS avg_rating
                FROM recipes r
                LEFT JOIN ratings rt ON r.id = rt.recipe_id
                GROUP BY r.id, r.title
                ORDER BY
                    CASE WHEN avg_rating IS NULL THEN 1 ELSE 0 END,
                    avg_rating DESC,
                    r.rowid DESC
                """
            ).fetchall()
        return HTMLResponse(content=render_recipes_overview_html(rows), status_code=200)
    except Exception:
        return Response(status_code=500)


@app.post("/recipes/upload", response_model=RecipeOut, status_code=201)
def upload_recipe(payload: RecipeUploadRequest):
    validate_recipe_input(payload)
    recipe_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, instructions) VALUES (?, ?, ?)",
            (recipe_id, payload.title.strip(), payload.instructions.strip()),
        )
        conn.executemany(
            "INSERT INTO ingredients (recipe_id, ingredient) VALUES (?, ?)",
            [(recipe_id, ingredient.strip()) for ingredient in payload.ingredients],
        )
        recipe = get_recipe_data(conn, recipe_id)

    return recipe


@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
def get_recipe(recipeId: str):
    with get_db() as conn:
        recipe = get_recipe_data(conn, recipeId)

    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return HTMLResponse(content=render_recipe_detail_html(recipe), status_code=200)


@app.post("/recipes/{recipeId}/comments", status_code=201)
def add_comment(recipeId: str, payload: CommentRequest):
    validate_comment_input(payload)

    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")
        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, payload.comment.strip()),
        )

    return JSONResponse(status_code=201, content={"message": "Comment added successfully"})


@app.post("/recipes/{recipeId}/ratings", status_code=201)
def add_rating(recipeId: str, payload: RatingRequest):
    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")
        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )

    return JSONResponse(status_code=201, content={"message": "Rating added successfully"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)