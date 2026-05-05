import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from threading import Lock
from typing import Deque, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_INSTRUCTIONS_LENGTH = 10000
MAX_INGREDIENT_LENGTH = 200
MAX_INGREDIENTS_COUNT = 100
MAX_COMMENT_LENGTH = 1000

RECIPES_OVERVIEW_LIMIT = 50
RECIPE_DETAIL_INGREDIENTS_LIMIT = 100
RECIPE_DETAIL_COMMENTS_LIMIT = 100

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_UPLOADS = 10
RATE_LIMIT_MAX_COMMENTS = 30
RATE_LIMIT_MAX_RATINGS = 60


app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


class SimpleRateLimiter:
    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            queue = self._events[key]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if len(queue) >= limit:
                return False
            queue.append(now)
            return True


rate_limiter = SimpleRateLimiter()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
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
                created_at INTEGER NOT NULL
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

        recipe_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()
        }
        if "created_at" not in recipe_columns:
            conn.execute("ALTER TABLE recipes ADD COLUMN created_at INTEGER")
            conn.execute(
                "UPDATE recipes SET created_at = rowid WHERE created_at IS NULL"
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe_id ON recipe_ingredients(recipe_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_recipe_id_id ON comments(recipe_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ratings_recipe_id ON ratings(recipe_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recipes_created_at ON recipes(created_at DESC)"
        )


@app.on_event("startup")
def startup() -> None:
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
        if not value.strip():
            raise ValueError("Invalid input")
        return value

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Invalid input")
        return value

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("Invalid input")
        for ingredient in value:
            if not isinstance(ingredient, str):
                raise ValueError("Invalid input")
            if not ingredient.strip():
                raise ValueError("Invalid input")
            if len(ingredient) > MAX_INGREDIENT_LENGTH:
                raise ValueError("Invalid input")
        return value


class CommentRequest(BaseModel):
    comment: str = Field(..., min_length=1, max_length=MAX_COMMENT_LENGTH, examples=["This recipe is amazing!"])

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Invalid input")
        return value


class RatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, examples=[5])


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut]
    avgRating: Optional[float] = None


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, action: str, limit: int) -> None:
    client_id = get_client_identifier(request)
    key = f"{action}:{client_id}"
    if not rate_limiter.check(key, limit, RATE_LIMIT_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too Many Requests")


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return row is not None


def fetch_recipe(conn: sqlite3.Connection, recipe_id: str) -> Optional[RecipeOut]:
    recipe_row = conn.execute(
        "SELECT id, title, instructions FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()

    if recipe_row is None:
        return None

    ingredient_rows = conn.execute(
        """
        SELECT ingredient
        FROM recipe_ingredients
        WHERE recipe_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (recipe_id, RECIPE_DETAIL_INGREDIENTS_LIMIT),
    ).fetchall()

    comment_rows = conn.execute(
        """
        SELECT comment
        FROM comments
        WHERE recipe_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (recipe_id, RECIPE_DETAIL_COMMENTS_LIMIT),
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
        ingredients=[row["ingredient"] for row in ingredient_rows],
        instructions=recipe_row["instructions"],
        comments=[CommentOut(comment=row["comment"]) for row in comment_rows],
        avgRating=avg_rating,
    )


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


@app.get("/recipes", response_class=HTMLResponse, responses={500: {"description": "Server error"}})
def get_recipes_overview():
    with get_db() as conn:
        recent_rows = conn.execute(
            """
            SELECT
                r.id,
                r.title,
                AVG(rt.rating) AS avg_rating
            FROM recipes r
            LEFT JOIN ratings rt ON r.id = rt.recipe_id
            GROUP BY r.id, r.title, r.created_at
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (RECIPES_OVERVIEW_LIMIT,),
        ).fetchall()

        top_rated_rows = conn.execute(
            """
            SELECT
                r.id,
                r.title,
                AVG(rt.rating) AS avg_rating
            FROM recipes r
            JOIN ratings rt ON r.id = rt.recipe_id
            GROUP BY r.id, r.title
            ORDER BY AVG(rt.rating) DESC, r.created_at DESC
            LIMIT ?
            """,
            (RECIPES_OVERVIEW_LIMIT,),
        ).fetchall()

    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Recipe Overview</title>",
        "</head>",
        "<body>",
        "<h1>Recipe Overview</h1>",
        "<h2>Recent Recipes</h2>",
    ]

    if not recent_rows:
        html_parts.append("<p>No recipes found.</p>")
    else:
        html_parts.append("<ul>")
        for row in recent_rows:
            title = html_escape(row["title"])
            recipe_id = html_escape(row["id"])
            avg_rating = row["avg_rating"]
            rating_text = "No ratings yet" if avg_rating is None else f"Average rating: {round(float(avg_rating), 2)}"
            html_parts.append(
                f"<li><a href='/recipes/{recipe_id}'>{title}</a> - {html_escape(rating_text)}</li>"
            )
        html_parts.append("</ul>")

    html_parts.append("<h2>Top-Rated Recipes</h2>")
    if not top_rated_rows:
        html_parts.append("<p>No rated recipes found.</p>")
    else:
        html_parts.append("<ul>")
        for row in top_rated_rows:
            title = html_escape(row["title"])
            recipe_id = html_escape(row["id"])
            avg_rating = round(float(row["avg_rating"]), 2)
            rating_text = f"Average rating: {avg_rating}"
            html_parts.append(
                f"<li><a href='/recipes/{recipe_id}'>{title}</a> - {html_escape(rating_text)}</li>"
            )
        html_parts.append("</ul>")

    html_parts.extend(["</body>", "</html>"])
    return HTMLResponse(content="\n".join(html_parts), status_code=200)


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=201,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(payload: RecipeUploadRequest, request: Request):
    enforce_rate_limit(request, "upload", RATE_LIMIT_MAX_UPLOADS)

    title = payload.title.strip()
    instructions = payload.instructions.strip()
    ingredients = [ingredient.strip() for ingredient in payload.ingredients]

    if not title or not instructions or not ingredients or any(not ingredient for ingredient in ingredients):
        raise HTTPException(status_code=400, detail="Invalid input")

    recipe_id = str(uuid.uuid4())
    created_at = int(time.time())

    with get_db() as conn:
        conn.execute(
            "INSERT INTO recipes (id, title, instructions, created_at) VALUES (?, ?, ?, ?)",
            (recipe_id, title, instructions, created_at),
        )
        conn.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient) VALUES (?, ?)",
            [(recipe_id, ingredient) for ingredient in ingredients],
        )

        recipe = fetch_recipe(conn, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=500, detail="Failed to create recipe")

    return recipe


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe(recipeId: str = Path(...)):
    with get_db() as conn:
        recipe_row = conn.execute(
            "SELECT id, title FROM recipes WHERE id = ?",
            (recipeId,),
        ).fetchone()

    if recipe_row is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    recipe_id = html_escape(recipe_row["id"])
    title = html_escape(recipe_row["title"])

    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        f"<title>{title}</title>",
        "</head>",
        "<body>",
        "<h1>Recipe Overview</h1>",
        f"<p><strong>Title:</strong> {title}</p>",
        f"<p><strong>Link:</strong> <a href='/recipes/{recipe_id}'>/recipes/{recipe_id}</a></p>",
        "</body>",
        "</html>",
    ]
    return HTMLResponse(content="\n".join(html_parts), status_code=200)


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=201,
    response_class=Response,
    responses={
        201: {"description": "Comment added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
def add_comment(recipeId: str, payload: CommentRequest, request: Request):
    enforce_rate_limit(request, "comment", RATE_LIMIT_MAX_COMMENTS)

    comment = payload.comment.strip()
    if not comment:
        raise HTTPException(status_code=400, detail="Invalid input")

    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
            (recipeId, comment),
        )

    return Response(status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=201,
    response_class=Response,
    responses={
        201: {"description": "Rating added successfully"},
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
def add_rating(recipeId: str, payload: RatingRequest, request: Request):
    enforce_rate_limit(request, "rating", RATE_LIMIT_MAX_RATINGS)

    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Invalid input")

    with get_db() as conn:
        if not recipe_exists(conn, recipeId):
            raise HTTPException(status_code=404, detail="Recipe not found")

        conn.execute(
            "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
            (recipeId, payload.rating),
        )

    return Response(status_code=201)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)