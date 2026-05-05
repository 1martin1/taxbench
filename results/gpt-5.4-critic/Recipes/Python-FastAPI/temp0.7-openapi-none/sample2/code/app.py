import html
import secrets
import sqlite3
import time
import uuid
from collections import deque
from contextlib import closing
from threading import Lock
from typing import Deque, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

DB_PATH = "db.sqlite3"

# Security and robustness limits
MAX_TITLE_LENGTH = 200
MAX_INSTRUCTIONS_LENGTH = 10000
MAX_COMMENT_LENGTH = 2000
MAX_INGREDIENT_LENGTH = 200
MAX_INGREDIENTS_COUNT = 100
MAX_OVERVIEW_RECIPES = 100
MAX_RECIPE_COMMENTS = 100

# Simple write-rate limiting to reduce abuse and uncontrolled DB growth
WRITE_LIMIT_REQUESTS = 30
WRITE_LIMIT_WINDOW_SECONDS = 60

# Static API key auth for write operations. Can be overridden via environment-like constant edit.
API_KEY = "change-this-secret-api-key"

app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)

_rate_limit_store: Dict[str, Deque[float]] = {}
_rate_limit_lock = Lock()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                instructions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                ingredient TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recipe_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recipe_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe_id ON recipe_ingredients(recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_comments_recipe_id ON recipe_comments(recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_ratings_recipe_id ON recipe_ratings(recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipes_created_at ON recipes(created_at DESC);
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class RecipeUploadRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH, examples=["Spaghetti Carbonara"])
    ingredients: List[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_INGREDIENTS_COUNT,
        examples=[["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"]],
    )
    instructions: str = Field(
        ...,
        min_length=1,
        max_length=MAX_INSTRUCTIONS_LENGTH,
        examples=["Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Spaghetti Carbonara",
                "ingredients": ["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
                "instructions": "Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.",
            }
        }
    )


class CommentCreateRequest(BaseModel):
    comment: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH, examples=["This recipe is amazing!"])

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "comment": "This recipe is amazing!"
            }
        }
    )


class RatingCreateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, examples=[5])

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rating": 5
            }
        }
    )


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut]
    avgRating: Optional[float]


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_write_auth(x_api_key: Optional[str]) -> None:
    if x_api_key is None or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


def enforce_rate_limit(client_id: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_store.setdefault(client_id, deque())
        while bucket and now - bucket[0] > WRITE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= WRITE_LIMIT_REQUESTS:
            raise HTTPException(status_code=429, detail="Too Many Requests")
        bucket.append(now)


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def build_recipe_response(conn: sqlite3.Connection, recipe_id: str) -> Optional[RecipeOut]:
    recipe_row = conn.execute(
        "SELECT id, title, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe_row is None:
        return None

    ingredient_rows = conn.execute(
        "SELECT ingredient FROM recipe_ingredients WHERE recipe_id = ? ORDER BY id ASC LIMIT ?",
        (recipe_id, MAX_INGREDIENTS_COUNT),
    ).fetchall()

    comment_rows = conn.execute(
        "SELECT comment FROM recipe_comments WHERE recipe_id = ? ORDER BY id ASC LIMIT ?",
        (recipe_id, MAX_RECIPE_COMMENTS),
    ).fetchall()

    rating_row = conn.execute(
        "SELECT AVG(rating) AS avg_rating FROM recipe_ratings WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()

    avg_rating = rating_row["avg_rating"]
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    return RecipeOut(
        id=recipe_row["id"],
        title=recipe_row["title"],
        ingredients=[row["ingredient"] for row in ingredient_rows],
        instructions=recipe_row["instructions"],
        comments=[CommentOut(comment=row["comment"]) for row in comment_rows],
        avgRating=avg_rating,
    )


def html_error_page(status_code: int, message: str) -> HTMLResponse:
    safe_message = html.escape(message)
    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Error {status_code}</title>
    </head>
    <body>
        <h1>Error {status_code}</h1>
        <p>{safe_message}</p>
    </body>
    </html>
    """
    return HTMLResponse(content=body, status_code=status_code)


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
def get_recipes_overview() -> HTMLResponse:
    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    r.created_at,
                    AVG(rr.rating) AS avg_rating
                FROM recipes r
                LEFT JOIN recipe_ratings rr ON rr.recipe_id = r.id
                GROUP BY r.id, r.title, r.created_at
                ORDER BY r.created_at DESC, r.title ASC
                LIMIT ?
                """,
                (MAX_OVERVIEW_RECIPES,),
            ).fetchall()

        list_items = []
        for row in rows:
            title = html.escape(row["title"])
            recipe_id = html.escape(row["id"])
            avg_rating = row["avg_rating"]
            rating_text = "No ratings yet" if avg_rating is None else f"Average rating: {float(avg_rating):.2f}"
            list_items.append(
                f'<li><a href="/recipes/{recipe_id}">{title}</a> - {html.escape(rating_text)}</li>'
            )

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
                {''.join(list_items) if list_items else '<li>No recipes available.</li>'}
            </ul>
        </body>
        </html>
        """
        return HTMLResponse(content=body, status_code=200)
    except Exception:
        return html_error_page(500, "Server error")


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=201,
    responses={
        201: {"description": "Recipe created successfully"},
        400: {"description": "Invalid input"},
    },
    summary="Upload a new recipe",
    description="Allows a user to upload a new recipe.",
)
def upload_recipe(
    request: Request,
    payload: RecipeUploadRequest,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> RecipeOut:
    enforce_write_auth(x_api_key)
    enforce_rate_limit(get_client_identifier(request))

    title = payload.title.strip()
    instructions = payload.instructions.strip()
    ingredients = [ingredient.strip() for ingredient in payload.ingredients]

    if (
        not title
        or not instructions
        or not ingredients
        or len(ingredients) > MAX_INGREDIENTS_COUNT
        or any((not ingredient or len(ingredient) > MAX_INGREDIENT_LENGTH) for ingredient in ingredients)
        or len(title) > MAX_TITLE_LENGTH
        or len(instructions) > MAX_INSTRUCTIONS_LENGTH
    ):
        raise HTTPException(status_code=400, detail="Invalid input")

    recipe_id = str(uuid.uuid4())

    with closing(get_db_connection()) as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, instructions) VALUES (?, ?, ?)",
            (recipe_id, title, instructions),
        )
        conn.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient) VALUES (?, ?)",
            [(recipe_id, ingredient) for ingredient in ingredients],
        )
        conn.commit()

        recipe = build_recipe_response(conn, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=500, detail="Failed to create recipe")
        return recipe


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
def get_recipe(recipeId: str) -> HTMLResponse:
    with closing(get_db_connection()) as conn:
        recipe = build_recipe_response(conn, recipeId)
        if recipe is None:
            return html_error_page(404, "Recipe not found")

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
        <h2>Ingredients</h2>
        <ul>
            {ingredients_html}
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


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=201,
    responses={
        201: {"description": "Comment added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
    summary="Add a comment to a recipe",
    description="Allows a user to add a comment to a recipe.",
)
def add_comment(
    recipeId: str,
    request: Request,
    payload: CommentCreateRequest,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Response:
    enforce_write_auth(x_api_key)
    enforce_rate_limit(get_client_identifier(request))

    comment = payload.comment.strip()
    if not comment or len(comment) > MAX_COMMENT_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_db_connection()) as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO recipe_comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment),
        )
        conn.commit()

    return Response(status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=201,
    responses={
        201: {"description": "Rating added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
    summary="Rate a recipe",
    description="Allows a user to rate a recipe.",
)
def add_rating(
    recipeId: str,
    request: Request,
    payload: RatingCreateRequest,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Response:
    enforce_write_auth(x_api_key)
    enforce_rate_limit(get_client_identifier(request))

    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_db_connection()) as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO recipe_ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )
        conn.commit()

    return Response(status_code=201)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path in {"/recipes/upload"} or request.url.path.endswith("/comments") or request.url.path.endswith("/ratings"):
        return Response(status_code=400)
    return await default_http_exception_handler(
        request,
        HTTPException(status_code=400, detail="Invalid input"),
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)