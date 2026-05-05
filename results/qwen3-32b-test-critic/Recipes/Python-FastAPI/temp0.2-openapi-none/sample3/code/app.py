from fastapi import FastAPI, HTTPException, status, Body, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import uuid
import jinja2
import sqlite3
import json

app = FastAPI()

# Jinja2 template with autoescape enabled
template_env = jinja2.Environment(loader=jinja2.DictLoader({
    'recipes_list': """
        <html>
            <head><title>Recipes</title></head>
            <body>
                <h1>Recent Recipes</h1>
                <ul>
                {% for recipe in recipes %}
                    <li><a href="/recipes/{{ recipe.id|e }}">{{ recipe.title|e }}</a></li>
                {% endfor %}
                </ul>
            </body>
        </html>
    """,
    'recipe_detail': """
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
                <h2>Average Rating: {{ recipe.avgRating|default('No ratings yet') }}</h2>
            </body>
        </html>
    """
}), autoescape=True)

# Create database tables on startup
@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL,
            avg_rating REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    """)
    conn.commit()
    conn.close()

# Pydantic models
class RecipeCreate(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str

class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[dict]
    avgRating: Optional[float]

@app.get("/recipes", response_class=HTMLResponse)
async def get_recipes():
    template = template_env.get_template('recipes_list')
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes")
    recipes = cursor.fetchall()
    conn.close()
    
    recipe_list = [{"id": r[0], "title": r[1]} for r in recipes]
    return HTMLResponse(template.render(recipes=recipe_list))

@app.post("/recipes/upload", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
async def upload_recipe(recipe: RecipeCreate):
    if not recipe.title or not recipe.ingredients or not recipe.instructions:
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO recipes (id, title, ingredients, instructions, avg_rating)
        VALUES (?, ?, ?, ?, ?)
    """, (recipe_id, recipe.title, ingredients_json, recipe.instructions, None))
    conn.commit()
    conn.close()
    
    return RecipeOut(
        id=recipe_id,
        title=recipe.title,
        ingredients=recipe.ingredients,
        instructions=recipe.instructions,
        comments=[],
        avgRating=None
    )

@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
async def get_recipe(recipeId: str = Path(..., description="ID of the recipe to retrieve")):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    
    # Get the recipe
    cursor.execute("SELECT id, title, ingredients, instructions, avg_rating FROM recipes WHERE id = ?", (recipeId,))
    recipe_row = cursor.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    recipe_id, title, ingredients_json, instructions, avg_rating = recipe_row
    ingredients = json.loads(ingredients_json)
    
    # Get comments
    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments = [{"comment": row[0]} for row in cursor.fetchall()]
    
    conn.close()
    
    recipe_data = {
        "id": recipe_id,
        "title": title,
        "ingredients": ingredients,
        "instructions": instructions,
        "comments": comments,
        "avgRating": avg_rating
    }
    
    template = template_env.get_template('recipe_detail')
    return HTMLResponse(template.render(recipe=recipe_data))

@app.post("/recipes/{recipeId}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    recipeId: str = Path(..., description="ID of the recipe to add comment to"),
    comment: str = Body(..., description="Comment text")
):
    if not comment.strip():
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    cursor.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment))
    conn.commit()
    conn.close()
    
    return {"status": "Comment added"}

@app.post("/recipes/{recipeId}/ratings", status_code=status.HTTP_201_CREATED)
async def add_rating(
    recipeId: str = Path(..., description="ID of the recipe to rate"),
    rating: int = Body(..., description="Rating value between 1 and 5")
):
    if not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating))
    
    cursor.execute("SELECT AVG(rating) FROM ratings WHERE recipe_id = ?", (recipeId,))
    avg_row = cursor.fetchone()
    avg = round(avg_row[0], 1) if avg_row[0] is not None else None
    
    cursor.execute("UPDATE recipes SET avg_rating = ? WHERE id = ?", (avg, recipeId))
    conn.commit()
    conn.close()
    
    return {"status": "Rating added", "avgRating": avg}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)