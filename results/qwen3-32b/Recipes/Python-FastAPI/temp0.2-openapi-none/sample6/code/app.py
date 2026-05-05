import sqlite3
import json
import uuid
from fastapi import FastAPI, HTTPException, Path, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import jinja2

app = FastAPI()

# Database setup
def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row  # to get rows as dictionaries
    return db

# Create tables on startup
@app.on_event("startup")
def create_tables():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL
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
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    db.commit()
    db.close()

# Pydantic models
class RecipeCreate(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str

class Recipe(RecipeCreate):
    id: str
    comments: List[Dict[str, str]]
    avgRating: Optional[float]

class CommentCreate(BaseModel):
    comment: str

class RatingCreate(BaseModel):
    rating: int

@app.get("/recipes", response_class=HTMLResponse)
async def get_recipes_overview():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM recipes")
    recipes_rows = cursor.fetchall()
    db.close()

    # Convert rows to a list of dictionaries
    recipes = [dict(row) for row in recipes_rows]

    # Parse ingredients JSON
    for recipe in recipes:
        recipe['ingredients'] = json.loads(recipe['ingredients'])

    # Render the HTML template
    template_str = """
    <!DOCTYPE html>
    <html>
    <head><title>Recipes</title></head>
    <body>
    <h1>Recipes</h1>
    <ul>
    {% for recipe in recipes %}
        <li><a href="/recipes/{{ recipe.id }}">{{ recipe.title }}</a></li>
    {% endfor %}
    </ul>
    </body>
    </html>
    """
    template = jinja2.Template(template_str)
    html_out = template.render(recipes=recipes)
    return HTMLResponse(content=html_out, status_code=200)

@app.post("/recipes/upload", status_code=201)
async def upload_recipe(recipe_data: RecipeCreate):
    db = get_db()
    cursor = db.cursor()
    # Generate a unique ID for the recipe
    recipe_id = str(uuid.uuid4())
    # Convert ingredients list to JSON string
    ingredients_json = json.dumps(recipe_data.ingredients)
    cursor.execute(
        "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
        (recipe_id, recipe_data.title, ingredients_json, recipe_data.instructions)
    )
    db.commit()
    db.close()

    # Return the created recipe
    return {
        "id": recipe_id,
        "title": recipe_data.title,
        "ingredients": recipe_data.ingredients,
        "instructions": recipe_data.instructions,
        "comments": [],
        "avgRating": None
    }

@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
async def get_recipe(recipeId: str):
    db = get_db()
    cursor = db.cursor()

    # Fetch the recipe
    cursor.execute("SELECT * FROM recipes WHERE id = ?", (recipeId,))
    recipe_row = cursor.fetchone()
    if not recipe_row:
        db.close()
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Fetch comments
    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments_rows = cursor.fetchall()
    comments = [dict(row)['comment'] for row in comments_rows]

    # Fetch ratings and calculate average
    cursor.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipeId,))
    ratings_rows = cursor.fetchall()
    ratings = [dict(row)['rating'] for row in ratings_rows]
    avg_rating = sum(ratings) / len(ratings) if ratings else None

    db.close()

    # Convert the recipe row to a dictionary
    recipe = dict(recipe_row)
    recipe['ingredients'] = json.loads(recipe['ingredients'])
    recipe['comments'] = [{"comment": comment} for comment in comments]
    recipe['avgRating'] = avg_rating

    # Render the HTML template
    template_str = """
    <!DOCTYPE html>
    <html>
    <head><title>{{ recipe.title }}</title></head>
    <body>
    <h1>{{ recipe.title }}</h1>
    <h2>Ingredients</h2>
    <ul>
    {% for ingredient in recipe.ingredients %}
        <li>{{ ingredient }}</li>
    {% endfor %}
    </ul>
    <h2>Instructions</h2>
    <p>{{ recipe.instructions }}</p>
    <h2>Comments</h2>
    <ul>
    {% for comment in recipe.comments %}
        <li>{{ comment.comment }}</li>
    {% endfor %}
    </ul>
    <h2>Average Rating</h2>
    <p>{% if recipe.avgRating is not none %}{{ recipe.avgRating }}{% else %}No ratings yet{% endif %}</p>
    </body>
    </html>
    """
    template = jinja2.Template(template_str)
    html_out = template.render(recipe=recipe)
    return HTMLResponse(content=html_out, status_code=200)

@app.post("/recipes/{recipeId}/comments", status_code=201)
async def add_comment(recipeId: str, comment_data: CommentCreate):
    db = get_db()
    cursor = db.cursor()

    # Check if the recipe exists
    cursor.execute("SELECT * FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        db.close()
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Insert the comment
    cursor.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (recipeId, comment_data.comment)
    )
    db.commit()
    db.close()

    return {"message": "Comment added"}

@app.post("/recipes/{recipeId}/ratings", status_code=201)
async def add_rating(recipeId: str, rating_data: RatingCreate):
    db = get_db()
    cursor = db.cursor()

    # Check if the recipe exists
    cursor.execute("SELECT * FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        db.close()
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Insert the rating
    cursor.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (recipeId, rating_data.rating)
    )
    db.commit()
    db.close()

    return {"message": "Rating added"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)