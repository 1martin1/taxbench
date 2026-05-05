import html
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_INSTRUCTIONS_LENGTH = 10000
MAX_COMMENT_LENGTH = 2000
MAX_INGREDIENTS_COUNT = 100
MAX_INGREDIENT_LENGTH = 200
OVERVIEW_SECTION_LIMIT = 20
DETAIL_COMMENTS_LIMIT = 100
DETAIL_INGREDIENTS_LIMIT = 100


app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


def custom_openapi() -> Dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    recipe_schema = (
        schema.get("components", {})
        .get("schemas", {})
        .get("RecipeOut")
    )
    if recipe_schema and "properties" in recipe_schema and "avgRating" in recipe_schema["properties"]:
        recipe_schema["properties"]["avgRating"] = {
            "type": "number | null",
            "example": 3.5,
        }

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


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
                instructions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ingredients_recipe_id ON ingredients(recipe_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_recipe_id ON comments(recipe_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ratings_recipe_id ON ratings(recipe_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recipes_created_at ON recipes(created_at, id)"
        )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


class RecipeUploadRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH, example="Spaghetti Carbonara")
    ingredients: List[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_INGREDIENTS_COUNT,
        example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
    )
    instructions: str = Field(
        ...,
        min_length=1,
        max_length=MAX_INSTRUCTIONS_LENGTH,
        example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.",
    )

    @field_validator("title", "instructions")
    @classmethod
    def validate_text_fields(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Invalid input")
        return stripped

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, value: List[str]) -> List[str]:
        cleaned: List[str] = []
        for ingredient in value:
            if not isinstance(ingredient, str):
                raise ValueError("Invalid input")
            stripped = ingredient.strip()
            if not stripped or len(stripped) > MAX_INGREDIENT_LENGTH:
                raise ValueError("Invalid input")
            cleaned.append(stripped)
        if not cleaned:
            raise ValueError("Invalid input")
        return cleaned


class CommentRequest(BaseModel):
    comment: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH, example="This recipe is amazing!")

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Invalid input")
        return stripped


class RatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, example=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut]
    avgRating: Optional[float]


def build_recipe_response(conn: sqlite3.Connection, recipe_id: str) -> Optional[RecipeOut]:
    recipe = conn.execute(
        "SELECT id, title, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe is None:
        return None

    ingredient_rows = conn.execute(
        """
        SELECT ingredient
        FROM ingredients
        WHERE recipe_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (recipe_id, DETAIL_INGREDIENTS_LIMIT),
    ).fetchall()
    comment_rows = conn.execute(
        """
        SELECT comment
        FROM comments
        WHERE recipe_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (recipe_id, DETAIL_COMMENTS_LIMIT),
    ).fetchall()
    rating_row = conn.execute(
        "SELECT AVG(rating) AS avg_rating FROM ratings WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()

    avg_rating = rating_row["avg_rating"]
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    return RecipeOut(
        id=recipe["id"],
        title=recipe["title"],
        ingredients=[row["ingredient"] for row in ingredient_rows],
        instructions=recipe["instructions"],
        comments=[CommentOut(comment=row["comment"]) for row in comment_rows],
        avgRating=avg_rating,
    )


@app.get("/recipes", response_class=HTMLResponse)
def get_recipes_overview():
    with get_db() as conn:
        recent_rows = conn.execute(
            """
            SELECT r.id, r.title
            FROM recipes r
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (OVERVIEW_SECTION_LIMIT,),
        ).fetchall()

        top_rated_rows = conn.execute(
            """
            SELECT
                r.id,
                r.title
            FROM recipes r
            JOIN ratings rt ON r.id = rt.recipe_id
            GROUP BY r.id, r.title
            ORDER BY AVG(rt.rating) DESC, COUNT(rt.id) DESC, r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (OVERVIEW_SECTION_LIMIT,),
        ).fetchall()

    recent_items = [
        f'<li><a href="/recipes/{html.escape(row["id"])}">{html.escape(row["title"])}</a></li>'
        for row in recent_rows
    ]
    top_rated_items = [
        f'<li><a href="/recipes/{html.escape(row["id"])}">{html.escape(row["title"])}</a></li>'
        for row in top_rated_rows
    ]

    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Recipe Overview</title>
    </head>
    <body>
        <h1>Recipe Overview</h1>
        <h2>Recent Recipes</h2>
        <ul>
            {''.join(recent_items) if recent_items else '<li>No recipes available.</li>'}
        </ul>
        <h2>Top-Rated Recipes</h2>
        <ul>
            {''.join(top_rated_items) if top_rated_items else '<li>No top-rated recipes available.</li>'}
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=body, status_code=200)


@app.post("/recipes/upload", response_model=RecipeOut, status_code=201)
def upload_recipe(payload: RecipeUploadRequest):
    recipe_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, instructions) VALUES (?, ?, ?)",
            (recipe_id, payload.title, payload.instructions),
        )
        conn.executemany(
            "INSERT INTO ingredients (recipe_id, ingredient) VALUES (?, ?)",
            [(recipe_id, ingredient) for ingredient in payload.ingredients],
        )
        recipe = build_recipe_response(conn, recipe_id)

    if recipe is None:
        raise HTTPException(status_code=500, detail="Failed to create recipe")
    return recipe


@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
def get_recipe(recipeId: str):
    with get_db() as conn:
        recipe = build_recipe_response(conn, recipeId)

    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    ingredients_html = "".join(f"<li>{html.escape(ingredient)}</li>" for ingredient in recipe.ingredients)
    comments_html = "".join(f"<li>{html.escape(comment.comment)}</li>" for comment in recipe.comments)
    avg_rating_text = "No ratings yet" if recipe.avgRating is None else f"{recipe.avgRating:.2f}"

    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{html.escape(recipe.title)}</title>
    </head>
    <body>
        <h1>{html.escape(recipe.title)}</h1>
        <p><strong>ID:</strong> {html.escape(recipe.id)}</p>
        <h2>Ingredients</h2>
        <ul>
            {ingredients_html if ingredients_html else '<li>No ingredients available.</li>'}
        </ul>
        <h2>Instructions</h2>
        <p>{html.escape(recipe.instructions)}</p>
        <h2>Average Rating</h2>
        <p>{html.escape(avg_rating_text)}</p>
        <h2>Comments</h2>
        <ul>
            {comments_html if comments_html else '<li>No comments yet.</li>'}
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=body, status_code=200)


@app.post("/recipes/{recipeId}/comments", status_code=201, response_class=Response)
def add_comment(recipeId: str, payload: CommentRequest):
    with get_db() as conn:
        recipe_exists = conn.execute(
            "SELECT 1 FROM recipes WHERE id = ?",
            (recipeId,),
        ).fetchone()
        if recipe_exists is None:
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, payload.comment),
        )

    return Response(status_code=201)


@app.post("/recipes/{recipeId}/ratings", status_code=201, response_class=Response)
def add_rating(recipeId: str, payload: RatingRequest):
    with get_db() as conn:
        recipe_exists = conn.execute(
            "SELECT 1 FROM recipes WHERE id = ?",
            (recipeId,),
        ).fetchone()
        if recipe_exists is None:
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )

    return Response(status_code=201)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)