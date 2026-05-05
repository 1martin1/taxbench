import json
import sqlite3
import time
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, conint
from jinja2 import Environment, select_autoescape
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

app = FastAPI(title="Recipe Sharing App API", version="1.0.0")


# ---------- Database utilities ----------
def get_db():
    conn = sqlite3.connect("db.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with sqlite3.connect("db.sqlite3") as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                ingredients TEXT NOT NULL,   -- JSON array
                instructions TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                comment TEXT NOT NULL,
                FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                FOREIGN KEY(recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Pydantic models ----------
class RecipeCreate(BaseModel):
    title: str = Field(..., example="Spaghetti Carbonara")
    ingredients: List[str] = Field(
        ..., example=["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"]
    )
    instructions: str = Field(
        ..., example="Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."
    )


class CommentCreate(BaseModel):
    comment: str = Field(..., example="This recipe is amazing!")


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


# ---------- Helper functions ----------
def fetch_recipe(conn: sqlite3.Connection, recipe_id: int) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
    return cur.fetchone()


def compute_average_rating(conn: sqlite3.Connection, recipe_id: int) -> Optional[float]:
    cur = conn.execute(
        "SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipe_id,)
    )
    row = cur.fetchone()
    return round(row["avg"], 2) if row["avg"] is not None else None


def fetch_comments(
    conn: sqlite3.Connection, recipe_id: int, limit: int = 100
) -> List[dict]:
    cur = conn.execute(
        "SELECT comment FROM comments WHERE recipe_id = ? ORDER BY id ASC LIMIT ?",
        (recipe_id, limit),
    )
    return [{"comment": r["comment"]} for r in cur.fetchall()]


# ---------- Jinja2 templates ----------
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

OVERVIEW_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Recipe Overview</title>
</head>
<body>
    <h1>Recipe Overview</h1>
    <ul>
    {% for recipe in recipes %}
        <li><a href="/recipes/{{ recipe.id }}">{{ recipe.title }}</a></li>
    {% else %}
        <li>No recipes yet.</li>
    {% endfor %}
    </ul>
</body>
</html>
"""

RECIPE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ title }}</title>
</head>
<body>
    <h1>{{ title }}</h1>
    <h2>Ingredients</h2>
    <ul>
    {% for ing in ingredients %}
        <li>{{ ing }}</li>
    {% endfor %}
    </ul>
    <h2>Instructions</h2>
    <p>{{ instructions|replace('\n', '<br/>')|safe }}</p>
    <h2>Average Rating</h2>
    <p>{{ avg_rating }}</p>

    <h2>Comments ({{ comments|length }})</h2>
    <ul>
    {% for c in comments %}
        <li>{{ c.comment }}</li>
    {% else %}
        <li>No comments yet.</li>
    {% endfor %}
    </ul>

    <h3>Add a Comment</h3>
    <form action="/recipes/{{ id }}/comments" method="post">
        <textarea name="comment" rows="3" cols="40" required></textarea><br/>
        <button type="submit">Submit Comment</button>
    </form>

    <h3>Rate this Recipe</h3>
    <form action="/recipes/{{ id }}/ratings" method="post">
        <label for="rating">Rating (1-5): </label>
        <input type="number" name="rating" min="1" max="5" required>
        <button type="submit">Submit Rating</button>
    </form>
</body>
</html>
"""

overview_template = jinja_env.from_string(OVERVIEW_TEMPLATE)
recipe_template = jinja_env.from_string(RECIPE_TEMPLATE)


# ---------- Middleware ----------
class BodySizeLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_size: int = 1_048_576):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next):
        # If Content-Length header is present, use it
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            return JSONResponse(
                {"detail": "Payload too large"}, status_code=413
            )
        # Otherwise, read the body and enforce size
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > self.max_size:
                return JSONResponse(
                    {"detail": "Payload too large"}, status_code=413
                )
        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}
        request._receive = receive
        response = await call_next(request)
        return response


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_requests: int = 20, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self.clients: dict[str, List[float]] = {}

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        now = time.time()
        timestamps = self.clients.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < self.window]
        if len(timestamps) >= self.max_requests:
            return JSONResponse(
                {"detail": "Too many requests"}, status_code=429
            )
        timestamps.append(now)
        self.clients[client_ip] = timestamps
        response = await call_next(request)
        return response


app.add_middleware(BodySizeLimiterMiddleware, max_size=1_048_576)
app.add_middleware(RateLimiterMiddleware, max_requests=20, window_seconds=60)


# ---------- Endpoints ----------
@app.get("/recipes", response_class=HTMLResponse)
def get_recipes_overview(db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 20")
    rows = cur.fetchall()
    recipes = [{"id": str(row["id"]), "title": row["title"]} for row in rows]
    html = overview_template.render(recipes=recipes)
    return HTMLResponse(content=html)


@app.post(
    "/recipes/upload",
    response_model=RecipeOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_recipe(
    recipe: RecipeCreate, db: sqlite3.Connection = Depends(get_db)
):
    cur = db.execute(
        "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
        (recipe.title, json.dumps(recipe.ingredients), recipe.instructions),
    )
    db.commit()
    recipe_id = cur.lastrowid
    return RecipeOut(
        id=str(recipe_id),
        title=recipe.title,
        ingredients=recipe.ingredients,
        instructions=recipe.instructions,
        comments=[],
        avgRating=None,
    )


@app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
def get_recipe(recipe_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not recipe_id.isdigit():
        raise HTTPException(status_code=404, detail="Recipe not found")
    rid = int(recipe_id)
    row = fetch_recipe(db, rid)
    if not row:
        raise HTTPException(status_code=404, detail="Recipe not found")
    ingredients = json.loads(row["ingredients"])
    comments = fetch_comments(db, rid, limit=100)
    avg = compute_average_rating(db, rid)
    avg_display = f"{avg} / 5" if avg is not None else "No ratings yet."
    html = recipe_template.render(
        id=str(rid),
        title=row["title"],
        ingredients=ingredients,
        instructions=row["instructions"],
        avg_rating=avg_display,
        comments=comments,
    )
    return HTMLResponse(content=html)


@app.post("/recipes/{recipe_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    recipe_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
):
    if not recipe_id.isdigit():
        raise HTTPException(status_code=404, detail="Recipe not found")
    rid = int(recipe_id)
    if not fetch_recipe(db, rid):
        raise HTTPException(status_code=404, detail="Recipe not found")

    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        comment_text = data.get("comment")
    else:
        form = await request.form()
        comment_text = form.get("comment")

    if not comment_text or not isinstance(comment_text, str):
        raise HTTPException(status_code=400, detail="Invalid input")

    db.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (rid, comment_text),
    )
    db.commit()
    return JSONResponse(
        content={"detail": "Comment added successfully"}, status_code=201
    )


@app.post("/recipes/{recipe_id}/ratings", status_code=status.HTTP_201_CREATED)
async def add_rating(
    recipe_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
):
    if not recipe_id.isdigit():
        raise HTTPException(status_code=404, detail="Recipe not found")
    rid = int(recipe_id)
    if not fetch_recipe(db, rid):
        raise HTTPException(status_code=404, detail="Recipe not found")

    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        rating_value = data.get("rating")
    else:
        form = await request.form()
        rating_value = form.get("rating")

    try:
        rating_int = int(rating_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid input")
    if rating_int < 1 or rating_int > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    db.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (rid, rating_int),
    )
    db.commit()
    return JSONResponse(
        content={"detail": "Rating added successfully"}, status_code=201
    )


# ---------- Run the server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)