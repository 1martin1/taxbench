import html
import sqlite3
import uuid
from contextlib import closing
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Path, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="Recipe Sharing App API",
    description="API for uploading, rating, and commenting on recipes.",
    version="1.0.0",
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class RecipeUploadRequest(BaseModel):
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


class CommentCreateRequest(BaseModel):
    comment: str = Field(..., min_length=1, example="This recipe is amazing!")


class RatingCreateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, example=5)


class CommentResponse(BaseModel):
    comment: str


class RecipeResponse(BaseModel):
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

    id: str
    title: str
    ingredients: list[str]
    instructions: str
    comments: list[CommentResponse]
    avgRating: Optional[float] = None


def serialize_recipe(recipe_id: str) -> Optional[RecipeResponse]:
    with closing(get_connection()) as conn:
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

    ingredients = [
        item.strip()
        for item in recipe_row["ingredients"].split("\n")
        if item.strip() != ""
    ]
    comments = [CommentResponse(comment=row["comment"]) for row in comment_rows]
    avg_rating = rating_row["avg_rating"]
    if avg_rating is not None:
        avg_rating = round(float(avg_rating), 2)

    return RecipeResponse(
        id=recipe_row["id"],
        title=recipe_row["title"],
        ingredients=ingredients,
        instructions=recipe_row["instructions"],
        comments=comments,
        avgRating=avg_rating,
    )


def recipe_exists(conn: sqlite3.Connection, recipe_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    return row is not None


def render_recipe_overview_html() -> str:
    with closing(get_connection()) as conn:
        recent_rows = conn.execute(
            """
            SELECT id, title, created_at
            FROM recipes
            ORDER BY datetime(created_at) DESC, id DESC
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
                CASE WHEN AVG(rt.rating) IS NULL THEN 1 ELSE 0 END,
                AVG(rt.rating) DESC,
                r.title ASC
            LIMIT 10
            """
        ).fetchall()

    recent_items = "".join(
        f'<li><a href="/recipes/{html.escape(row["id"], quote=True)}">{html.escape(row["title"])}</a></li>'
        for row in recent_rows
    )
    top_rated_items = "".join(
        (
            f'<li><a href="/recipes/{html.escape(row["id"], quote=True)}">{html.escape(row["title"])}</a>'
            + (
                f' - Rating: {round(float(row["avg_rating"]), 2)}'
                if row["avg_rating"] is not None
                else " - Rating: No ratings yet"
            )
            + "</li>"
        )
        for row in top_rated_rows
    )

    if not recent_items:
        recent_items = "<li>No recipes available.</li>"
    if not top_rated_items:
        top_rated_items = "<li>No recipes available.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Recipe Overview</title>
</head>
<body>
    <h1>Recipe Sharing App</h1>
    <h2>Recent Recipes</h2>
    <ul>
        {recent_items}
    </ul>
    <h2>Top Rated Recipes</h2>
    <ul>
        {top_rated_items}
    </ul>
</body>
</html>"""


def render_recipe_detail_html(recipe: RecipeResponse) -> str:
    ingredients_html = "".join(
        f"<li>{html.escape(ingredient)}</li>" for ingredient in recipe.ingredients
    )
    comments_html = "".join(
        f"<li>{html.escape(comment.comment)}</li>" for comment in recipe.comments
    )

    if not comments_html:
        comments_html = "<li>No comments yet.</li>"

    avg_rating_text = (
        str(recipe.avgRating) if recipe.avgRating is not None else "No ratings yet"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{html.escape(recipe.title)}</title>
</head>
<body>
    <h1>{html.escape(recipe.title)}</h1>
    <p><strong>Recipe ID:</strong> {html.escape(recipe.id)}</p>
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
    <p><a href="/recipes">Back to overview</a></p>
</body>
</html>"""


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
        return HTMLResponse(content=render_recipe_overview_html(), status_code=200)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Server error") from exc


@app.post(
    "/recipes/upload",
    response_model=RecipeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Recipe created successfully"},
        400: {"description": "Invalid input"},
    },
    summary="Upload a new recipe",
    description="Allows a user to upload a new recipe.",
)
def upload_recipe(payload: RecipeUploadRequest) -> RecipeResponse:
    title = payload.title.strip()
    instructions = payload.instructions.strip()
    ingredients = [item.strip() for item in payload.ingredients if item.strip()]

    if not title or not instructions or not ingredients:
        raise HTTPException(status_code=400, detail="Invalid input")

    recipe_id = str(uuid.uuid4())

    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT INTO recipes (id, title, ingredients, instructions)
            VALUES (?, ?, ?, ?)
            """,
            (recipe_id, title, "\n".join(ingredients), instructions),
        )
        conn.commit()

    recipe = serialize_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=500, detail="Server error")
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
def get_recipe(
    recipeId: str = Path(..., description="Recipe ID"),
) -> HTMLResponse:
    recipe = serialize_recipe(recipeId)
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
def add_comment(
    payload: CommentCreateRequest,
    recipeId: str = Path(..., description="Recipe ID"),
) -> JSONResponse:
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

    return JSONResponse(
        status_code=201,
        content={"message": "Comment added successfully"},
    )


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
def add_rating(
    payload: RatingCreateRequest,
    recipeId: str = Path(..., description="Recipe ID"),
) -> JSONResponse:
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

    return JSONResponse(
        status_code=201,
        content={"message": "Rating added successfully"},
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)