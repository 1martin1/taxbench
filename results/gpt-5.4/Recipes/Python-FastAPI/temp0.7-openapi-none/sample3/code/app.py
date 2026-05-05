import json
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Path, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict


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
        conn.execute("PRAGMA foreign_keys = ON")
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
                ingredients TEXT NOT NULL,
                instructions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: list[str]
    instructions: str
    comments: list[CommentOut]
    avgRating: Optional[float] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "12345",
                "title": "Spaghetti Carbonara",
                "ingredients": ["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
                "instructions": "Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.",
                "comments": [{"comment": "This recipe is amazing!"}],
                "avgRating": 3.5,
            }
        }
    )


class RecipeCreate(BaseModel):
    title: str = Field(..., min_length=1, examples=["Spaghetti Carbonara"])
    ingredients: list[str] = Field(
        ...,
        min_length=1,
        examples=[["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"]],
    )
    instructions: str = Field(
        ...,
        min_length=1,
        examples=["Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."],
    )


class CommentCreate(BaseModel):
    comment: str = Field(..., min_length=1, examples=["This recipe is amazing!"])


class RatingCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, examples=[5])


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def fetch_recipe(conn: sqlite3.Connection, recipe_id: str) -> Optional[RecipeOut]:
    recipe_row = conn.execute(
        "SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()

    if recipe_row is None:
        return None

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

    return RecipeOut(
        id=recipe_row["id"],
        title=recipe_row["title"],
        ingredients=json.loads(recipe_row["ingredients"]),
        instructions=recipe_row["instructions"],
        comments=[CommentOut(comment=row["comment"]) for row in comment_rows],
        avgRating=avg_rating,
    )


def render_recipe_overview_html(recipes: list[sqlite3.Row]) -> str:
    items = []
    for recipe in recipes:
        recipe_id = recipe["id"]
        title = recipe["title"]
        items.append(f'<li><a href="/recipes/{recipe_id}">{title}</a></li>')

    recipe_list = "\n".join(items) if items else "<li>No recipes available.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recipe Overview</title>
</head>
<body>
  <h1>Recipe Overview</h1>
  <p>Recent and top-rated recipes.</p>
  <ul>
    {recipe_list}
  </ul>
</body>
</html>"""


def render_recipe_detail_html(recipe: RecipeOut) -> str:
    ingredients_html = "\n".join(f"<li>{ingredient}</li>" for ingredient in recipe.ingredients)
    comments_html = (
        "\n".join(f"<li>{comment.comment}</li>" for comment in recipe.comments)
        if recipe.comments
        else "<li>No comments yet.</li>"
    )
    avg_rating = "No ratings yet" if recipe.avgRating is None else str(recipe.avgRating)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{recipe.title}</title>
</head>
<body>
  <h1>{recipe.title}</h1>
  <h2>Ingredients</h2>
  <ul>
    {ingredients_html}
  </ul>
  <h2>Instructions</h2>
  <p>{recipe.instructions}</p>
  <h2>Average Rating</h2>
  <p>{avg_rating}</p>
  <h2>Comments</h2>
  <ul>
    {comments_html}
  </ul>
</body>
</html>"""


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "Overview of recipes, including just titles and links to the full recipe",
            "content": {
                "text/html": {
                    "schema": {
                        "type": "string",
                        "description": "HTML page with recipe overview",
                    }
                }
            },
        },
        500: {"description": "Server error"},
    },
    summary="Get an overview of recipes",
    description="Retrieve a summary of recent and top-rated recipes.",
)
def get_recipes_overview():
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.title, COALESCE(AVG(rt.rating), 0) AS avg_rating, r.created_at
                FROM recipes r
                LEFT JOIN ratings rt ON r.id = rt.recipe_id
                GROUP BY r.id, r.title, r.created_at
                ORDER BY avg_rating DESC, r.created_at DESC
                LIMIT 20
                """
            ).fetchall()
        return HTMLResponse(content=render_recipe_overview_html(rows), status_code=200)
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
def upload_recipe(payload: RecipeCreate):
    try:
        recipe_id = str(uuid.uuid4())
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO recipes (id, title, ingredients, instructions)
                VALUES (?, ?, ?, ?)
                """,
                (
                    recipe_id,
                    payload.title.strip(),
                    json.dumps([item.strip() for item in payload.ingredients]),
                    payload.instructions.strip(),
                ),
            )
            recipe = fetch_recipe(conn, recipe_id)

        if recipe is None:
            raise HTTPException(status_code=500, detail="Failed to create recipe")

        return recipe
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "Recipe details, ratings, and comments",
            "content": {
                "text/html": {
                    "schema": {
                        "type": "string",
                        "description": "HTML page with recipe overview",
                    }
                }
            },
        },
        404: {"description": "Recipe not found"},
    },
    summary="Get a recipe",
    description="Retrieve a recipe by its ID.",
)
def get_recipe(recipeId: str = Path(..., description="Recipe ID")):
    with get_db() as conn:
        recipe = fetch_recipe(conn, recipeId)

    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return HTMLResponse(content=render_recipe_detail_html(recipe), status_code=200)


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
def add_comment(recipeId: str, payload: CommentCreate):
    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        comment_text = payload.comment.strip()
        if not comment_text:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment_text),
        )

    return JSONResponse(status_code=201, content={"message": "Comment added successfully"})


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
def add_rating(recipeId: str, payload: RatingCreate):
    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        if payload.rating < 1 or payload.rating > 5:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )

    return JSONResponse(status_code=201, content={"message": "Rating added successfully"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)