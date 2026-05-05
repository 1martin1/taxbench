import html
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import closing
from typing import Any, Deque, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = "db.sqlite3"

MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_TITLE_LENGTH = 200
MAX_INSTRUCTIONS_LENGTH = 10000
MAX_COMMENT_LENGTH = 2000
MAX_INGREDIENTS = 100
MAX_INGREDIENT_LENGTH = 200
MAX_OVERVIEW_RECENT = 10
MAX_OVERVIEW_TOP_RATED = 10
MAX_RECIPE_COMMENTS_RENDERED = 100
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_MUTATIONS = 30

app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)

_rate_limit_store: Dict[str, Deque[float]] = defaultdict(deque)


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
                ingredients TEXT NOT NULL,
                instructions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

            CREATE INDEX IF NOT EXISTS idx_comments_recipe_id_id ON comments(recipe_id, id);
            CREATE INDEX IF NOT EXISTS idx_ratings_recipe_id ON ratings(recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipes_created_at ON recipes(created_at DESC);
            """
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, __: RequestValidationError) -> Response:
    return Response(status_code=400)


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return Response(status_code=400)
        except ValueError:
            return Response(status_code=400)
    return await call_next(request)


def enforce_rate_limit(request: Request) -> None:
    client_host = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _rate_limit_store[client_host]

    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_MAX_MUTATIONS:
        raise HTTPException(status_code=400, detail="Invalid input")

    bucket.append(now)


def validate_recipe_id(recipe_id: str) -> str:
    if len(recipe_id) > 36:
        raise HTTPException(status_code=404, detail="Recipe not found")
    try:
        parsed = uuid.UUID(recipe_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Recipe not found")
    return str(parsed)


class RecipeUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH, examples=["Spaghetti Carbonara"])
    ingredients: List[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_INGREDIENTS,
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
        if not value or len(value) > MAX_TITLE_LENGTH:
            raise ValueError("Invalid input")
        return value

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_INSTRUCTIONS_LENGTH:
            raise ValueError("Invalid input")
        return value

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, value: List[str]) -> List[str]:
        cleaned = []
        if not value or len(value) > MAX_INGREDIENTS:
            raise ValueError("Invalid input")
        for item in value:
            if not isinstance(item, str):
                raise ValueError("Invalid input")
            stripped = item.strip()
            if not stripped or len(stripped) > MAX_INGREDIENT_LENGTH:
                raise ValueError("Invalid input")
            cleaned.append(stripped)
        return cleaned


class CommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH, examples=["This recipe is amazing!"])

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_COMMENT_LENGTH:
            raise ValueError("Invalid input")
        return value


class RatingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(..., ge=1, le=5, examples=[5])


class CommentResponse(BaseModel):
    comment: str


class RecipeResponse(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentResponse]
    avgRating: Optional[float] = None


def serialize_ingredients(ingredients: List[str]) -> str:
    return "\n".join(ingredients)


def deserialize_ingredients(raw: str) -> List[str]:
    if not raw:
        return []
    return [line for line in raw.split("\n") if line != ""]


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def build_recipe_response(conn: sqlite3.Connection, recipe_id: str, comment_limit: int = MAX_RECIPE_COMMENTS_RENDERED) -> RecipeResponse:
    recipe_row = conn.execute(
        "SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()

    if recipe_row is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    comment_rows = conn.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC LIMIT ?",
        (recipe_id, comment_limit),
    ).fetchall()

    rating_row = conn.execute(
        "SELECT AVG(rating) AS avg_rating FROM ratings WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()

    avg_rating = rating_row["avg_rating"]
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    return RecipeResponse(
        id=recipe_row["id"],
        title=recipe_row["title"],
        ingredients=deserialize_ingredients(recipe_row["ingredients"]),
        instructions=recipe_row["instructions"],
        comments=[CommentResponse(comment=row["comment"]) for row in comment_rows],
        avgRating=avg_rating,
    )


def render_overview_html(recent_recipes: List[sqlite3.Row], top_rated_recipes: List[sqlite3.Row]) -> str:
    def render_items(recipes: List[sqlite3.Row]) -> str:
        items = []
        for recipe in recipes:
            recipe_id = html.escape(recipe["id"])
            title = html.escape(recipe["title"])
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
        return "\n".join(items) if items else "<li>No recipes available.</li>"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>Recipe Overview</title>
    </head>
    <body>
        <h1>Recipe Overview</h1>
        <h2>Recent Recipes</h2>
        <ul>
            {render_items(recent_recipes)}
        </ul>
        <h2>Top-Rated Recipes</h2>
        <ul>
            {render_items(top_rated_recipes)}
        </ul>
    </body>
    </html>
    """


def render_recipe_html(recipe: RecipeResponse) -> str:
    ingredients_html = "".join(f"<li>{html.escape(item)}</li>" for item in recipe.ingredients)
    comments_html = (
        "".join(f"<li>{html.escape(comment.comment)}</li>" for comment in recipe.comments)
        if recipe.comments
        else "<li>No comments yet.</li>"
    )
    avg_rating_text = "No ratings yet" if recipe.avgRating is None else f"{recipe.avgRating:.2f}"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
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
            {comments_html}
        </ul>
    </body>
    </html>
    """


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
def get_recipes_overview() -> HTMLResponse:
    try:
        with closing(get_connection()) as conn:
            recent_rows = conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    AVG(rt.rating) AS avg_rating
                FROM recipes r
                LEFT JOIN ratings rt ON rt.recipe_id = r.id
                GROUP BY r.id, r.title, r.created_at
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (MAX_OVERVIEW_RECENT,),
            ).fetchall()

            top_rated_rows = conn.execute(
                """
                SELECT
                    r.id,
                    r.title,
                    AVG(rt.rating) AS avg_rating
                FROM recipes r
                LEFT JOIN ratings rt ON rt.recipe_id = r.id
                GROUP BY r.id, r.title, r.created_at
                ORDER BY
                    CASE WHEN AVG(rt.rating) IS NULL THEN 1 ELSE 0 END ASC,
                    AVG(rt.rating) DESC,
                    r.created_at DESC
                LIMIT ?
                """,
                (MAX_OVERVIEW_TOP_RATED,),
            ).fetchall()

        return HTMLResponse(content=render_overview_html(recent_rows, top_rated_rows), status_code=200)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="Server error") from exc


@app.post(
    "/recipes/upload",
    response_model=RecipeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "Recipe created successfully",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "example": "12345"},
                            "title": {"type": "string", "example": "Spaghetti Carbonara"},
                            "ingredients": {
                                "type": "array",
                                "items": {"type": "string"},
                                "example": ["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
                            },
                            "instructions": {
                                "type": "string",
                                "example": "Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.",
                            },
                            "comments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "comment": {"type": "string", "example": "This recipe is amazing!"}
                                    },
                                },
                            },
                            "avgRating": {"nullable": True, "type": "number", "example": 3.5},
                        },
                        "required": ["id", "title", "ingredients", "instructions", "comments", "avgRating"],
                    }
                }
            },
        },
        400: {"description": "Invalid input"},
    },
    summary="Upload a new recipe",
    description="Allows a user to upload a new recipe.",
)
def upload_recipe(payload: RecipeUploadRequest, request: Request) -> RecipeResponse:
    enforce_rate_limit(request)

    recipe_id = str(uuid.uuid4())

    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
            (recipe_id, payload.title, serialize_ingredients(payload.ingredients), payload.instructions),
        )
        conn.commit()
        return build_recipe_response(conn, recipe_id)


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
def get_recipe(recipeId: str = Path(..., description="Recipe ID", min_length=36, max_length=36)) -> HTMLResponse:
    validated_recipe_id = validate_recipe_id(recipeId)
    with closing(get_connection()) as conn:
        recipe = build_recipe_response(conn, validated_recipe_id)
        return HTMLResponse(content=render_recipe_html(recipe), status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    responses={
        201: {"description": "Comment added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
    summary="Add a comment to a recipe",
    description="Allows a user to add a comment to a recipe.",
)
def add_comment(
    payload: CommentCreateRequest,
    request: Request,
    recipeId: str = Path(..., description="Recipe ID", min_length=36, max_length=36),
) -> Response:
    enforce_rate_limit(request)
    validated_recipe_id = validate_recipe_id(recipeId)

    with closing(get_connection()) as conn:
        if not recipe_exists(conn, validated_recipe_id):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (validated_recipe_id, payload.comment),
        )
        conn.commit()

    return Response(status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    response_class=Response,
    responses={
        201: {"description": "Rating added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
    summary="Rate a recipe",
    description="Allows a user to rate a recipe.",
)
def add_rating(
    payload: RatingCreateRequest,
    request: Request,
    recipeId: str = Path(..., description="Recipe ID", min_length=36, max_length=36),
) -> Response:
    enforce_rate_limit(request)
    validated_recipe_id = validate_recipe_id(recipeId)

    with closing(get_connection()) as conn:
        if not recipe_exists(conn, validated_recipe_id):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (validated_recipe_id, payload.rating),
        )
        conn.commit()

    return Response(status_code=201)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)