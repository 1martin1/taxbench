from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Optional, Annotated
import sqlite3
import json
import time
from jinja2 import Template
from fastapi.responses import HTMLResponse
from fastapi import Response

app = FastAPI()

# Rate limiting configuration
request_counts = {}
RATE_LIMIT = 10  # requests per minute
TIME_WINDOW = 60  # seconds

def rate_limiter(request: Request):
    client_host = request.client.host
    current_time = time.time()
    
    # Clean up old requests
    if client_host in request_counts:
        request_counts[client_host] = [t for t in request_counts[client_host] if current_time - t <= TIME_WINDOW]
    
    if len(request_counts.get(client_host, [])) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests, please try again later")
    
    request_counts.setdefault(client_host, []).append(current_time)

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    """)
    conn.commit()
    conn.close()

class RecipeCreate(BaseModel):
    title: str = Field(..., max_length=255)
    ingredients: List[str] = Field(..., max_items=50)
    
    @field_validator('ingredients')
    def validate_ingredients(cls, v):
        for i in v:
            if len(i) > 100:
                raise ValueError("Ingredient too long (max 100 characters)")
        return v
    
    instructions: str = Field(..., max_length=10000)

class Recipe(RecipeCreate):
    id: str
    comments: List[Dict[str, str]]
    avgRating: Optional[float]

class CommentCreate(BaseModel):
    comment: str = Field(..., max_length=1000)

class RatingCreate(BaseModel):
    rating: int

OVERVIEW_TEMPLATE = Template("""
<!DOCTYPE html>
<html>
<head><title>Recipes</title></head>
<body>
<h1>Recipes</h1>
<ul>
{% for recipe in recipes %}
<li><a href="/recipes/{{ recipe.id }}">{{ recipe.title|e }}</a></li>
{% endfor %}
</ul>
</body>
</html>
""")

RECIPE_TEMPLATE = Template("""
<!DOCTYPE html>
<html>
<head><title>{{ recipe.title|e }}</title></head>
<body>
<h1>{{ recipe.title|e }}</h1>
<h2>Ingredients</h2>
<ul>
{% for ingredient in recipe.ingredients %}
<li>{{ ingredient|e }}</li>
{% endfor %}
</ul>
<h2>Instructions</h2>
<p>{{ recipe.instructions|e }}</p>
<h2>Comments</h2>
<ul>
{% for comment in recipe.comments %}
<li>{{ comment.comment|e }}</li>
{% endfor %}
</ul>
<h2>Average Rating: {{ recipe.avgRating if recipe.avgRating is not none else 'No ratings yet' }}</h2>
</body>
</html>
""")

@app.get("/recipes", response_class=HTMLResponse)
async def get_recipes_overview():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes")
    recipes = cursor.fetchall()
    conn.close()
    recipe_list = [{"id": str(r[0]), "title": r[1]} for r in recipes]
    html_content = OVERVIEW_TEMPLATE.render(recipes=recipe_list)
    return HTMLResponse(content=html_content, status_code=200)

@app.post("/recipes/upload", response_model=Recipe, status_code=201)
async def upload_recipe(recipe_data: RecipeCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    ingredients_json = json.dumps(recipe_data.ingredients)
    cursor.execute("""
        INSERT INTO recipes (title, ingredients, instructions)
        VALUES (?, ?, ?)
    """, (recipe_data.title, ingredients_json, recipe_data.instructions))
    recipe_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return Recipe(
        id=str(recipe_id),
        title=recipe_data.title,
        ingredients=recipe_data.ingredients,
        instructions=recipe_data.instructions,
        comments=[],
        avgRating=None
    )

@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
async def get_recipe(recipeId: str):
    try:
        recipe_id_int = int(recipeId)
    except ValueError:
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipe_id_int,))
    recipe_row = cursor.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_id, title, ingredients_json, instructions = recipe_row
    ingredients = json.loads(ingredients_json)
    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipe_id_int,))
    comments = cursor.fetchall()
    comments_list = [{"comment": c[0]} for c in comments]
    cursor.execute("SELECT AVG(rating) FROM ratings WHERE recipe_id = ?", (recipe_id_int,))
    avg_rating = cursor.fetchone()[0]
    avg_rating = round(avg_rating, 1) if avg_rating else None
    conn.close()
    recipe = {
        "id": str(recipe_id),
        "title": title,
        "ingredients": ingredients,
        "instructions": instructions,
        "comments": comments_list,
        "avgRating": avg_rating
    }
    html_content = RECIPE_TEMPLATE.render(recipe=recipe)
    return HTMLResponse(content=html_content, status_code=200)

@app.post("/recipes/{recipeId}/comments", status_code=201, dependencies=[Depends(rate_limiter)])
async def add_comment(recipeId: str, comment_data: CommentCreate, request: Request):
    try:
        recipe_id_int = int(recipeId)
    except ValueError:
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id_int,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipe_id_int, comment_data.comment))
    conn.commit()
    conn.close()
    return Response(status_code=201)

@app.post("/recipes/{recipeId}/ratings", status_code=201, dependencies=[Depends(rate_limiter)])
async def add_rating(recipeId: str, rating_data: RatingCreate, request: Request):
    try:
        recipe_id_int = int(recipeId)
    except ValueError:
        raise HTTPException(status_code=404, detail="Recipe not found")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id_int,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipe_id_int, rating_data.rating))
    conn.commit()
    conn.close()
    return Response(status_code=201)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)