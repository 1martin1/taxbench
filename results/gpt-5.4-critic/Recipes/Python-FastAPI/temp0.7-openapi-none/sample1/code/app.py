import html
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_TITLE_LENGTH = 200
MAX_INSTRUCTIONS_LENGTH = 10000
MAX_COMMENT_LENGTH = 2000
MAX_INGREDIENTS_COUNT = 100
MAX_INGREDIENT_LENGTH = 200
MAX_COMMENTS_RETURNED = 200
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
AUTH_TOKEN = "recipe-app-token"

_rate_limit_store: Dict[str, List[float]] = {}
_rate_limit_lock = threading.Lock()


app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, action: str) -> None:
    identifier = f"{action}:{get_client_identifier(request)}"
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    with _rate_limit_lock:
        entries = _rate_limit_store.get(identifier, [])
        entries = [timestamp for timestamp in entries if timestamp >= cutoff]
        if len(entries) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=400, detail="Invalid input")
        entries.append(now)
        _rate_limit_store[identifier] = entries


def require_auth(authorization: Optional[str]) -> None:
    expected = f"Bearer {AUTH_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=400, detail="Invalid input")


@contextmanager
def get_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
        conn.commit()
    except sqlite3.Error:
        if conn is not None:
            conn.rollback()
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        if conn is not None:
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

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invalid input")
        return value

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invalid input")
        return value

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, value: List[str]) -> List[str]:
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("Invalid input")
            stripped = item.strip()
            if not stripped:
                continue
            if len(stripped) > MAX_INGREDIENT_LENGTH:
                raise ValueError("Invalid input")
            cleaned.append(stripped)
        if not cleaned:
            raise ValueError("Invalid input")
        if len(cleaned) > MAX_INGREDIENTS_COUNT:
            raise ValueError("Invalid input")
        return cleaned


class CommentCreateRequest(BaseModel):
    comment: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH, examples=["This recipe is amazing!"])

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invalid input")
        return value


class RatingCreateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, examples=[5])


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut]
    avgRating: Optional[float] = Field(
        default=None,
        json_schema_extra={"nullable": True},
    )


def serialize_ingredients(ingredients: List[str]) -> str:
    return json.dumps(ingredients, ensure_ascii=False, separators=(",", ":"))


def deserialize_ingredients(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return [item for item in raw.split("\n") if item != ""]


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def get_recipe_data(conn: sqlite3.Connection, recipe_id: str, comments_limit: int = MAX_COMMENTS_RETURNED) -> Optional[RecipeOut]:
    recipe_row = conn.execute(
        "SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe_row is None:
        return None

    comment_rows = conn.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC LIMIT ?",
        (recipe_id, comments_limit),
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
        ingredients=deserialize_ingredients(recipe_row["ingredients"]),
        instructions=recipe_row["instructions"],
        comments=[CommentOut(comment=row["comment"]) for row in comment_rows],
        avgRating=avg_rating,
    )


def render_recipes_overview_html(recipes: List[sqlite3.Row]) -> str:
    items = []
    for recipe in recipes:
        recipe_id = html.escape(recipe["id"])
        title = html.escape(recipe["title"])
        avg_rating = recipe["avg_rating"]
        avg_rating_display = "No ratings yet" if avg_rating is None else f"Average rating: {float(avg_rating):.2f}"
        items.append(
            f'<li><a href="/recipes/{recipe_id}">{title}</a> - {html.escape(avg_rating_display)}</li>'
        )

    body = "\n".join(items) if items else "<li>No recipes available.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Recipe Overview</title>
</head>
<body>
  <h1>Recipe Overview</h1>
  <p>Recent and top-rated recipes</p>
  <ul>
    {body}
  </ul>
</body>
</html>"""


def render_recipe_detail_html(recipe: RecipeOut) -> str:
    ingredients_html = "".join(f"<li>{html.escape(item)}</li>" for item in recipe.ingredients)
    comments_html = (
        "".join(f"<li>{html.escape(comment.comment)}</li>" for comment in recipe.comments)
        if recipe.comments
        else "<li>No comments yet.</li>"
    )
    avg_rating_display = "No ratings yet" if recipe.avgRating is None else f"{recipe.avgRating:.2f}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{html.escape(recipe.title)}</title>
</head>
<body>
  <h1>{html.escape(recipe.title)}</h1>
  <p><strong>ID:</strong> {html.escape(recipe.id)}</p>
  <h2>Ingredients</h2>
  <ul>
    {ingredients_html}
  </ul>
  <h2>Instructions</h2>
  <p>{html.escape(recipe.instructions)}</p>
  <h2>Average Rating</h2>
  <p>{html.escape(avg_rating_display)}</p>
  <h2>Comments</h2>
  <ul>
    {comments_html}
  </ul>
</body>
</html>"""


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(status_code=400, content={"detail": "Invalid input"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid input"})

    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse(status_code=400, content={"detail": "Invalid input"})

        async def receive() -> Dict[str, object]:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]

    return await call_next(request)


@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "Overview of recipes, including just titles and links to the full recipe",
            "content": {"text/html": {"schema": {"type": "string", "description": "HTML page with recipe overview"}}},
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
                SELECT r.id, r.title, AVG(rt.rating) AS avg_rating, MAX(r.created_at) AS created_at
                FROM recipes r
                LEFT JOIN ratings rt ON r.id = rt.recipe_id
                GROUP BY r.id, r.title
                ORDER BY
                    CASE WHEN avg_rating IS NULL THEN 1 ELSE 0 END,
                    avg_rating DESC,
                    created_at DESC
                LIMIT 20
                """
            ).fetchall()
        return HTMLResponse(content=render_recipes_overview_html(rows), status_code=200)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Server error")


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
    payload: RecipeUploadRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    enforce_rate_limit(request, "upload")

    recipe_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
            (recipe_id, payload.title, serialize_ingredients(payload.ingredients), payload.instructions),
        )
        recipe = get_recipe_data(conn, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=500, detail="Server error")

    return recipe


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "Recipe details, ratings, and comments",
            "content": {"text/html": {"schema": {"type": "string", "description": "HTML page with recipe overview"}}},
        },
        404: {"description": "Recipe not found"},
    },
    summary="Get a recipe",
    description="Retrieve a recipe by its ID.",
)
def get_recipe(recipeId: str):
    with get_db() as conn:
        recipe = get_recipe_data(conn, recipeId)

    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return HTMLResponse(content=render_recipe_detail_html(recipe), status_code=200)


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
    payload: CommentCreateRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    enforce_rate_limit(request, "comment")

    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")
        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, payload.comment),
        )

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
    payload: RatingCreateRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    enforce_rate_limit(request, "rating")

    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")
        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )

    return Response(status_code=201)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, __: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if exc.status_code in {400, 404, 500}:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception):
    return JSONResponse(status_code=500, content={"detail": "Server error"})


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

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
            "type": "number",
            "nullable": True,
            "example": 3.5,
        }

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)