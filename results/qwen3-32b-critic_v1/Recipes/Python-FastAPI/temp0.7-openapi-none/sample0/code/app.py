import sqlite3
import uuid
import json
import html
import time
from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional

app = FastAPI()

# Global dictionary for rate limiting
request_counts = {}

def rate_limit(ip: str, limit: int = 10, window_seconds: int = 60):
    now = time.time()
    if ip in request_counts:
        # Remove timestamps outside the window
        request_counts[ip] = [ts for ts in request_counts[ip] if now - ts <= window_seconds]
    if len(request_counts.get(ip, [])) >= limit:
        return False
    if ip not in request_counts:
        request_counts[ip] = []
    request_counts[ip].append(now)
    return True

@app.on_event("startup")
def create_tables():
    try:
        conn = sqlite3.connect("db.sqlite3")
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS recipes
                     (id TEXT PRIMARY KEY, title TEXT, ingredients TEXT, instructions TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS comments
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT, comment TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ratings
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT, rating INTEGER)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise

class RecipeUpload(BaseModel):
    title: str = Field(..., max_length=200)
    ingredients: List[str] = Field(..., max_length=50)
    instructions: str = Field(..., max_length=2000)

    @field_validator('ingredients')
    def check_ingredient_length(cls, v):
        for i, ing in enumerate(v):
            if len(ing) > 100:
                raise ValueError(f"Ingredient at index {i} is too long (max 100 characters)")
        return v

class RecipeResponse(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[dict] = []
    avgRating: Optional[float] = None

class CommentAdd(BaseModel):
    comment: str = Field(..., max_length=500)

class RatingAdd(BaseModel):
    rating: int = Field(..., ge=1, le=5)

@app.post("/recipes/upload", status_code=201, response_model=RecipeResponse)
async def upload_recipe(recipe: RecipeUpload):
    try:
        recipe_id = str(uuid.uuid4())
        ingredients_json = json.dumps(recipe.ingredients)
        conn = sqlite3.connect("db.sqlite3")
        c = conn.cursor()
        c.execute("INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
                  (recipe_id, recipe.title, ingredients_json, recipe.instructions))
        conn.commit()
        conn.close()
        return {
            "id": recipe_id,
            "title": recipe.title,
            "ingredients": recipe.ingredients,
            "instructions": recipe.instructions,
            "comments": [],
            "avgRating": None
        }
    except Exception as e:
        print(f"Database error in upload_recipe: {e}")
        raise HTTPException(status_code=500, detail="Server error")

@app.get("/recipes")
async def get_recipes_overview():
    try:
        conn = sqlite3.connect("db.sqlite3")
        c = conn.cursor()
        c.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 10")
        recent_recipes = c.fetchall()
        c.execute("""
            SELECT r.id, r.title, AVG(ra.rating) as avg_rating
            FROM recipes r
            LEFT JOIN ratings ra ON r.id = ra.recipe_id
            GROUP BY r.id
            ORDER BY avg_rating DESC
            LIMIT 5
        """)
        top_recipes = c.fetchall()
        conn.close()
        
        recent_html = "<h2>Recent Recipes</h2><ul>" + "".join(f"<li><a href='/recipes/{html.escape(rid)}'>{html.escape(title)}</a></li>" for rid, title in recent_recipes) + "</ul>"
        top_html = "<h2>Top Recipes</h2><ul>" + "".join(f"<li><a href='/recipes/{html.escape(rid)}'>{html.escape(title)}</a></li>" for rid, title in top_recipes) + "</ul>"
        
        html_content = f"""
        <html>
            <head><title>Recipe Overview</title></head>
            <body>
                {recent_html}
                {top_html}
            </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)
    except Exception as e:
        print(f"Database error in get_recipes_overview: {e}")
        raise HTTPException(status_code=500, detail="Server error")

@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
async def get_recipe(recipeId: str):
    try:
        conn = sqlite3.connect("db.sqlite3")
        c = conn.cursor()
        c.execute("SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipeId,))
        recipe_row = c.fetchone()
        if not recipe_row:
            conn.close()
            raise HTTPException(status_code=404, detail="Recipe not found")
        recipe_id, title, ingredients_json, instructions = recipe_row
        ingredients = json.loads(ingredients_json)
        
        c.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
        comments_rows = c.fetchall()
        comments = [{"comment": comment} for (comment,) in comments_rows]
        
        c.execute("SELECT AVG(rating) FROM ratings WHERE recipe_id = ?", (recipeId,))
        avg_rating = c.fetchone()[0]
        avg_rating = round(avg_rating, 1) if avg_rating else None
        
        conn.close()
        
        html_content = f"""
        <html>
            <head><title>{html.escape(title)}</title></head>
            <body>
                <h1>{html.escape(title)}</h1>
                <h2>Ingredients</h2>
                <ul>
                    {''.join(f"<li>{html.escape(ing)}</li>" for ing in ingredients)}
                </ul>
                <h2>Instructions</h2>
                <p>{html.escape(instructions)}</p>
                <h2>Comments</h2>
                <ul>
                    {''.join(f"<li>{html.escape(cmt['comment'])}</li>" for cmt in comments)}
                </ul>
                <h2>Average Rating</h2>
                <p>{avg_rating if avg_rating is not None else 'No ratings yet'}</p>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)
    except Exception as e:
        print(f"Database error in get_recipe: {e}")
        raise HTTPException(status_code=500, detail="Server error")

@app.post("/recipes/{recipeId}/comments", status_code=201)
async def add_comment(recipeId: str, comment: CommentAdd, request: Request):
    ip = request.client.host
    if not rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    try:
        conn = sqlite3.connect("db.sqlite3")
        c = conn.cursor()
        c.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Recipe not found")
        c.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment.comment))
        conn.commit()
        conn.close()
        return {"status": "Comment added"}
    except Exception as e:
        print(f"Database error in add_comment: {e}")
        raise HTTPException(status_code=500, detail="Server error")

@app.post("/recipes/{recipeId}/ratings", status_code=201)
async def add_rating(recipeId: str, rating: RatingAdd, request: Request):
    ip = request.client.host
    if not rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    try:
        conn = sqlite3.connect("db.sqlite3")
        c = conn.cursor()
        c.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not c.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Recipe not found")
        c.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating.rating))
        conn.commit()
        conn.close()
        return {"status": "Rating added"}
    except Exception as e:
        print(f"Database error in add_rating: {e}")
        raise HTTPException(status_code=500, detail="Server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)