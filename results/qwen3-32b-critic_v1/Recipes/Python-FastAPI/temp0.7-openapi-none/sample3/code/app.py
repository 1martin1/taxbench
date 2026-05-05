from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, validator
from typing import List, Optional
import sqlite3
import uuid
import json
from fastapi.responses import HTMLResponse
from html import escape
import time
from threading import Lock

app = FastAPI()

# Rate limiting setup
request_counts = {}
rate_limit_lock = Lock()
RATE_LIMIT = 10  # Max requests per minute per IP
RATE_WINDOW = 60  # Seconds

class UploadRecipeModel(BaseModel):
    title: str = Field(max_length=100)
    ingredients: List[str] = Field(max_length=20)
    instructions: str = Field(max_length=1000)

    @validator('ingredients', each_item=True)
    def validate_ingredient_length(cls, v):
        if len(v) > 100:
            raise ValueError('Ingredient too long')
        return v

class CommentModel(BaseModel):
    comment: str = Field(max_length=500)

class RatingModel(BaseModel):
    rating: int = Field(ge=1, le=5)

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL,
            avg_rating REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    conn.commit()
    conn.close()

def apply_rate_limit(ip: str):
    with rate_limit_lock:
        now = time.time()
        # Remove old entries
        for ip_key in list(request_counts.keys()):
            if now - request_counts[ip_key][0] > RATE_WINDOW:
                del request_counts[ip_key]
        
        if ip in request_counts:
            count, _ = request_counts[ip]
            if count >= RATE_LIMIT:
                raise HTTPException(status_code=429, detail="Too many requests")
            request_counts[ip] = (count + 1, now)
        else:
            request_counts[ip] = (1, now)

@app.get("/recipes")
async def get_recipes():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes ORDER BY id DESC LIMIT 10")
    recipes = cursor.fetchall()
    conn.close()
    
    html_content = """
    <html>
    <head><title>Recipe Overview</title></head>
    <body>
    <h1>Recent Recipes</h1>
    <ul>
    """
    for recipe_id, title in recipes:
        html_content += f"<li><a href='/recipes/{escape(recipe_id)}'>{escape(title)}</a></li>"
    html_content += """
    </ul>
    </body>
    </html>
    """
    return HTMLResponse(html_content)

@app.post("/recipes/upload")
async def upload_recipe(recipe: UploadRecipeModel):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO recipes (id, title, ingredients, instructions, avg_rating) VALUES (?, ?, ?, ?, ?)",
        (recipe_id, recipe.title, ingredients_json, recipe.instructions, None)
    )
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

@app.get("/recipes/{recipeId}")
async def get_recipe(recipeId: str):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, ingredients, instructions, avg_rating FROM recipes WHERE id = ?", (recipeId,))
    recipe_row = cursor.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_id, title, ingredients_json, instructions, avg_rating = recipe_row
    ingredients = json.loads(ingredients_json)
    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    avg_html = "N/A"
    if avg_rating is not None:
        avg_html = f"{avg_rating:.1f}"
    
    html = f"""
    <html>
    <head><title>{escape(title)}</title></head>
    <body>
    <h1>{escape(title)}</h1>
    <h2>Ingredients</h2>
    <ul>{"".join([f"<li>{escape(ing)}</li>" for ing in ingredients])}</ul>
    <h2>Instructions</h2>
    <p>{escape(instructions)}</p>
    <h2>Comments</h2>
    <ul>{"".join([f"<li>{escape(comment)}</li>" for comment in comments])}</ul>
    <h2>Average Rating: {avg_html}</h2>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.post("/recipes/{recipeId}/comments")
async def add_comment(recipeId: str, comment: CommentModel, request: Request):
    ip = request.client.host
    apply_rate_limit(ip)
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment.comment))
    conn.commit()
    conn.close()
    return {"message": "Comment added"}

@app.post("/recipes/{recipeId}/ratings")
async def add_rating(recipeId: str, rating: RatingModel, request: Request):
    ip = request.client.host
    apply_rate_limit(ip)
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    # Use transaction with immediate lock to prevent race conditions
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating.rating))
    cursor.execute("UPDATE recipes SET avg_rating = (SELECT AVG(rating) FROM ratings WHERE recipe_id = ?) WHERE id = ?", (recipeId, recipeId))
    conn.commit()
    conn.close()
    return {"message": "Rating added"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)