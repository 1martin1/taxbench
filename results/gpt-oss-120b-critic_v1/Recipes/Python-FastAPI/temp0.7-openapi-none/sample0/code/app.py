import json
import html
import sqlite3
from typing import List, Optional, Generator

from fastapi import FastAPI, HTTPException, Path, status, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint, constr

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    FastAPI dependency that provides a SQLite connection.
    Uses `check_same_thread=False` to allow usage in async contexts.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialise the SQLite database with the required tables.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                ingredients TEXT NOT NULL, -- JSON array string
                instructions TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                comment TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- Pydantic Schemas ----------
class RecipeCreate(BaseModel):
    title: constr(max_length=200) = Field(..., example="Spaghetti Carbonara")
    ingredients: List[constr(max_length=100)] = Field(
        ..., example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"]
    )
    instructions: constr(max_length=2000) = Field(
        ..., example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."
    )


class CommentCreate(BaseModel):
    comment: constr(max_length=500) = Field(..., example="This recipe is amazing!")


class RatingCreate(BaseModel):
    rating: conint(ge=1, le=5) = Field(..., example=5)


class CommentOut(BaseModel):
    comment: str


class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[CommentOut] = []
    avgRating: Optional[float] = None


# ---------- Helper Functions ----------
def _parse_recipe_id(recipe_id: str) -> int:
    """
    Validate that the supplied recipe_id string represents an integer.
    """
    if not recipe_id.isdigit():
        raise HTTPException(status_code=404, detail="Recipe not found")
    return int(recipe_id)


def fetch_recipe(conn: sqlite3.Connection, recipe_id: str) -> Optional[sqlite3.Row]:
    rid = _parse_recipe_id(recipe_id)
    cur = conn.cursor()
    cur.execute("SELECT * FROM recipes WHERE id = ?", (rid,))
    return cur.fetchone()


def fetch_comments(conn: sqlite3.Connection, recipe_id: str) -> List[sqlite3.Row]:
    rid = _parse_recipe_id(recipe_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC", (rid,)
    )
    return cur.fetchall()


def fetch_average_rating(conn: sqlite3.Connection, recipe_id: str) -> Optional[float]:
    rid = _parse_recipe_id(recipe_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (rid,)
    )
    row = cur.fetchone()
    return round(row["avg"], 2) if row["avg"] is not None else None


def build_recipe_out(conn: sqlite3.Connection, row: sqlite3.Row) -> RecipeOut:
    comments_rows = fetch_comments(conn, str(row["id"]))
    comments = [CommentOut(comment=r["comment"]) for r in comments_rows]
    avg_rating = fetch_average_rating(conn, str(row["id"]))
    ingredients = json.loads(row["ingredients"])
    return RecipeOut(
        id=str(row["id"]),
        title=row["title"],
        ingredients=ingredients,
        instructions=row["instructions"],
        comments=comments,
        avgRating=avg_rating,
    )


# ---------- Endpoints ----------
@app.get(
    "/recipes",
    response_class=HTMLResponse,
    responses={500: {"description": "Server error"}},
)
def get_recipes_overview(db: sqlite3.Connection = Depends(get_db)):
    """
    Retrieve a summary of recent and top‑rated recipes.
    Returns a simple HTML page with titles linking to the detail page.
    """
    try:
        cur = db.cursor()
        # Recent recipes (last 10)
        cur.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 10")
        recent = cur.fetchall()

        # Top rated recipes (by avg rating, limit 10)
        cur.execute(
            """
            SELECT r.id, r.title, AVG(rt.rating) as avg_rating
            FROM recipes r
            JOIN ratings rt ON r.id = rt.recipe_id
            GROUP BY r.id
            ORDER BY avg_rating DESC
            LIMIT 10
            """
        )
        top = cur.fetchall()

        html_parts = [
            "<html><head><title>Recipes Overview</title></head><body>",
            "<h1>Recent Recipes</h1><ul>",
        ]
        for rec in recent:
            title = html.escape(rec["title"])
            html_parts.append(
                f'<li><a href="/recipes/{rec["id"]}">{title}</a></li>'
            )
        html_parts.append("</ul>")

        html_parts.append("<h1>Top Rated Recipes</h1><ul>")
        for rec in top:
            title = html.escape(rec["title"])
            avg = round(rec["avg_rating"], 2) if rec["avg_rating"] is not None else "N/A"
            html_parts.append(
                f'<li><a href="/recipes/{rec["id"]}">{title} (Avg rating: {avg})</a></li>'
            )
        html_parts.append("</ul></body></html>")
        return HTMLResponse(content="".join(html_parts), status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid input"}},
)
def upload_recipe(payload: RecipeCreate, db: sqlite3.Connection = Depends(get_db)):
    """
    Upload a new recipe.
    """
    cur = db.cursor()
    cur.execute(
        "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
        (payload.title, json.dumps(payload.ingredients), payload.instructions),
    )
    recipe_id = cur.lastrowid
    db.commit()

    recipe_row = fetch_recipe(db, str(recipe_id))
    if not recipe_row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created recipe")
    return build_recipe_out(db, recipe_row)


@app.get(
    "/recipes/{recipeId}",
    response_class=HTMLResponse,
    responses={404: {"description": "Recipe not found"}},
)
def get_recipe_detail(
    recipeId: str = Path(..., description="The ID of the recipe to retrieve"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Retrieve a recipe by its ID.
    Returns an HTML page with details, comments and average rating.
    """
    recipe_row = fetch_recipe(db, recipeId)
    if not recipe_row:
        raise HTTPException(status_code=404, detail="Recipe not found")

    recipe = build_recipe_out(db, recipe_row)

    # Escape user-provided content
    esc_title = html.escape(recipe.title)
    esc_instructions = html.escape(recipe.instructions)

    html_parts = [
        f"<html><head><title>{esc_title}</title></head><body>",
        f"<h1>{esc_title}</h1>",
        "<h2>Ingredients</h2><ul>",
    ]
    for ing in recipe.ingredients:
        html_parts.append(f"<li>{html.escape(ing)}</li>")
    html_parts.append("</ul>")
    html_parts.append(f"<h2>Instructions</h2><p>{esc_instructions}</p>")

    avg = f"{recipe.avgRating}" if recipe.avgRating is not None else "No ratings yet"
    html_parts.append(f"<h3>Average Rating: {avg}</h3>")

    html_parts.append("<h2>Comments</h2><ul>")
    if recipe.comments:
        for c in recipe.comments:
            html_parts.append(f"<li>{html.escape(c.comment)}</li>")
    else:
        html_parts.append("<li>No comments yet.</li>")
    html_parts.append("</ul>")

    # Simple forms for manual testing (they submit form‑encoded data)
    html_parts.append(
        f"""
        <h3>Add a Comment</h3>
        <form action="/recipes/{recipeId}/comments" method="post">
            <textarea name="comment" rows="3" cols="40" required></textarea><br/>
            <button type="submit">Submit Comment</button>
        </form>
        <h3>Rate this Recipe</h3>
        <form action="/recipes/{recipeId}/ratings" method="post">
            <input type="number" name="rating" min="1" max="5" required/>
            <button type="submit">Submit Rating</button>
        </form>
        """
    )
    html_parts.append("</body></html>")
    return HTMLResponse(content="".join(html_parts))


@app.post(
    "/recipes/{recipeId}/comments",
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
async def add_comment(
    request: Request,
    recipeId: str = Path(..., description="Recipe ID to comment on"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Add a comment to a recipe.
    Accepts JSON body or form‑encoded data.
    """
    # Try JSON first
    try:
        payload = await request.json()
        comment_data = CommentCreate(**payload)
    except Exception:
        # Fallback to form data
        form = await request.form()
        if "comment" not in form:
            raise HTTPException(status_code=400, detail="Missing comment")
        comment_data = CommentCreate(comment=form["comment"])

    if not fetch_recipe(db, recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    cur = db.cursor()
    cur.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (_parse_recipe_id(recipeId), comment_data.comment),
    )
    db.commit()
    return JSONResponse(content={"detail": "Comment added"}, status_code=201)


@app.post(
    "/recipes/{recipeId}/ratings",
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid input"},
        404: {"description": "Recipe not found"},
    },
)
async def add_rating(
    request: Request,
    recipeId: str = Path(..., description="Recipe ID to rate"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Add a rating to a recipe.
    Accepts JSON body or form‑encoded data.
    """
    # Try JSON first
    try:
        payload = await request.json()
        rating_data = RatingCreate(**payload)
    except Exception:
        # Fallback to form data
        form = await request.form()
        if "rating" not in form:
            raise HTTPException(status_code=400, detail="Missing rating")
        try:
            rating_int = int(form["rating"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Rating must be an integer")
        rating_data = RatingCreate(rating=rating_int)

    if not fetch_recipe(db, recipeId):
        raise HTTPException(status_code=404, detail="Recipe not found")
    cur = db.cursor()
    cur.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (_parse_recipe_id(recipeId), rating_data.rating),
    )
    db.commit()
    return JSONResponse(content={"detail": "Rating added"}, status_code=201)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)