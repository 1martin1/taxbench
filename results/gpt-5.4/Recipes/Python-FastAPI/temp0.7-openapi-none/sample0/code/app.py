import html
import sqlite3
import uuid
from contextlib import closing
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict


DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


class RecipeCreate(BaseModel):
    title: str = Field(..., min_length=1, example="Spaghetti Carbonara")
    ingredients: list[str] = Field(
        ...,
        min_length=1,
        example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
    )
    instructions: str = Field(
        ...,
        min_length=1,
        example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.",
    )


class CommentCreate(BaseModel):
    comment: str = Field(..., min_length=1, example="This recipe is amazing!")


class RatingCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, example=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    ingredients: list[str]
    instructions: str
    comments: list[CommentOut]
    avgRating: Optional[float] = None


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_connection()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                ingredient TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def build_recipe(conn: sqlite3.Connection, recipe_id: str) -> Optional[dict]:
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

    rating_row = conn.execute(
        "SELECT AVG(rating) AS avg_rating FROM ratings WHERE recipe_id = ?",
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


@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "Overview of recipes, including just titles and links to the full recipe",
            "content": {
                "text/html": {
                    "schema": {"type": "string", "description": "HTML page with recipe overview"}
                }
            },
        },
        500: {"description": "Server error"},
    },
    summary="Get an overview of recipes",
    description="Retrieve a summary of recent and top-rated recipes.",
)
def get_recipes_overview() -> HTMLResponse:
    try:
        with closing(get_connection()) as conn:
            recent_rows = conn.execute(
                """
                SELECT id, title
                FROM recipes
                ORDER BY rowid DESC
                LIMIT 10
                """
            ).fetchall()

            top_rated_rows = conn.execute(
                """
                SELECT r.id, r.title, AVG(rt.rating) AS avg_rating
                FROM recipes r
                LEFT JOIN ratings rt ON r.id = rt.recipe_id
                GROUP BY r.id, r.title
                ORDER BY
                    CASE WHEN avg_rating IS NULL THEN 1 ELSE 0 END ASC,
                    avg_rating DESC,
                    r.rowid DESC
                LIMIT 10
                """
            ).fetchall()

        recent_items = "".join(
            f'<li><a href="/recipes/{html.escape(row["id"])}">{html.escape(row["title"])}</a></li>'
            for row in recent_rows
        )
        top_rated_items = "".join(
            (
                f'<li><a href="/recipes/{html.escape(row["id"])}">{html.escape(row["title"])}</a>'
                + (
                    f" - Rating: {float(row['avg_rating']):.2f}"
                    if row["avg_rating"] is not None
                    else " - Rating: N/A"
                )
                + "</li>"
            )
            for row in top_rated_rows
        )

        body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Recipe Overview</title>
</head>
<body>
    <h1>Recipe Overview</h1>
    <h2>Recent Recipes</h2>
    <ul>{recent_items or "<li>No recipes available.</li>"}</ul>
    <h2>Top Rated Recipes</h2>
    <ul>{top_rated_items or "<li>No recipes available.</li>"}</ul>
</body>
</html>"""
        return HTMLResponse(content=body, status_code=status.HTTP_200_OK)
    except Exception:
        raise HTTPException(status_code=500, detail="Server error")


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Recipe created successfully"},
        400: {"description": "Invalid input"},
    },
    summary="Upload a new recipe",
    description="Allows a user to upload a new recipe.",
)
def upload_recipe(payload: RecipeCreate) -> RecipeOut:
    title = payload.title.strip()
    instructions = payload.instructions.strip()
    ingredients = [ingredient.strip() for ingredient in payload.ingredients]

    if not title or not instructions or not ingredients or any(not item for item in ingredients):
        raise HTTPException(status_code=400, detail="Invalid input")

    recipe_id = str(uuid.uuid4())

    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, instructions) VALUES (?, ?, ?)",
            (recipe_id, title, instructions),
        )
        conn.executemany(
            "INSERT INTO ingredients (recipe_id, ingredient) VALUES (?, ?)",
            [(recipe_id, ingredient) for ingredient in ingredients],
        )
        conn.commit()

        recipe = build_recipe(conn, recipe_id)

    return RecipeOut(**recipe)


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "Recipe details, ratings, and comments",
            "content": {
                "text/html": {
                    "schema": {"type": "string", "description": "HTML page with recipe overview"}
                }
            },
        },
        404: {"description": "Recipe not found"},
    },
    summary="Get a recipe",
    description="Retrieve a recipe by its ID.",
)
def get_recipe(recipeId: str = Path(...)) -> HTMLResponse:
    with closing(get_connection()) as conn:
        recipe = build_recipe(conn, recipeId)

    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    ingredients_html = "".join(
        f"<li>{html.escape(ingredient)}</li>" for ingredient in recipe["ingredients"]
    )
    comments_html = "".join(
        f"<li>{html.escape(comment['comment'])}</li>" for comment in recipe["comments"]
    )

    avg_rating_text = (
        f"{recipe['avgRating']:.2f}" if recipe["avgRating"] is not None else "N/A"
    )

    body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{html.escape(recipe["title"])}</title>
</head>
<body>
    <h1>{html.escape(recipe["title"])}</h1>
    <p><strong>ID:</strong> {html.escape(recipe["id"])}</p>
    <h2>Ingredients</h2>
    <ul>{ingredients_html}</ul>
    <h2>Instructions</h2>
    <p>{html.escape(recipe["instructions"])}</p>
    <h2>Average Rating</h2>
    <p>{avg_rating_text}</p>
    <h2>Comments</h2>
    <ul>{comments_html or "<li>No comments yet.</li>"}</ul>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=status.HTTP_200_OK)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Comment added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
    summary="Add a comment to a recipe",
    description="Allows a user to add a comment to a recipe.",
)
def add_comment(recipeId: str, payload: CommentCreate) -> Response:
    comment = payload.comment.strip()
    if not comment:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_connection()) as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment),
        )
        conn.commit()

    return Response(status_code=status.HTTP_201_CREATED)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Rating added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
    summary="Rate a recipe",
    description="Allows a user to rate a recipe.",
)
def add_rating(recipeId: str, payload: RatingCreate) -> Response:
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_connection()) as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )
        conn.commit()

    return Response(status_code=status.HTTP_201_CREATED)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(_, exc: HTTPException):
    if exc.status_code in {400, 404, 500}:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)