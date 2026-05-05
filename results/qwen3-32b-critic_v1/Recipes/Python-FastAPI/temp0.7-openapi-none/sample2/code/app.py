from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from typing import List, Optional, Dict
import sqlite3
import json
import uuid
from fastapi.responses import HTMLResponse
from jinja2 import Environment, select_autoescape, Template

app = FastAPI()

# Initialize database on startup
@app.on_event("startup")
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL,
            avg_rating REAL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    conn.commit()
    conn.close()

# Jinja2 templates
env = Environment(autoescape=select_autoescape(['html']))
recipes_overview_template = env.from_string('''
<html>
    <body>
        <h1>Recent Recipes</h1>
        <ul>
            {% for recipe in recipes %}
                <li><a href="/recipes/{{ recipe.id }}">{{ recipe.title|e }}</a></li>
            {% endfor %}
        </ul>
    </body>
</html>
''')

recipe_detail_template = env.from_string('''
<html>
    <body>
        <h1>{{ recipe.title|e }}</h1>
        <h2>Ingredients</h2>
        <ul>
            {% for ing in recipe.ingredients %}
                <li>{{ ing|e }}</li>
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
        <h2>Average Rating</h2>
        <p>{{ recipe.avgRating if recipe.avgRating is not none else "No ratings yet" }}</p>
    </body>
</html>
''')

# Pydantic models with input validation
class RecipeCreate(BaseModel):
    title: str = Field(..., description="Recipe title", max_length=200)
    ingredients: List[str] = Field(..., description="List of ingredients", max_items=50)
    instructions: str = Field(..., description="Preparation instructions", max_length=5000)

    @model_validator(mode='after')
    def validate_ingredients(self) -> 'RecipeCreate':
        if any(len(ingredient) > 100 for ingredient in self.ingredients):
            raise ValueError("Ingredient items must not exceed 100 characters")
        return self

class CommentCreate(BaseModel):
    comment: str = Field(..., description="User comment", max_length=1000)

class RatingCreate(BaseModel):
    rating: int = Field(..., description="Recipe rating (1-5)", ge=1, le=5)

class RecipeResponse(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[Dict[str, str]]
    avgRating: Optional[float]

@app.get("/recipes")
async def get_recipes_overview():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('SELECT id, title FROM recipes')
    recipes = [{"id": r[0], "title": r[1]} for r in c.fetchall()]
    conn.close()
    html = recipes_overview_template.render(recipes=recipes)
    return HTMLResponse(content=html, status_code=200)

@app.post("/recipes/upload", status_code=201)
async def upload_recipe(recipe_data: RecipeCreate):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe_data.ingredients)
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        INSERT INTO recipes (id, title, ingredients, instructions, avg_rating)
        VALUES (?, ?, ?, ?, ?)
    ''', (recipe_id, recipe_data.title, ingredients_json, recipe_data.instructions, None))
    conn.commit()
    conn.close()
    return RecipeResponse(
        id=recipe_id,
        title=recipe_data.title,
        ingredients=recipe_data.ingredients,
        instructions=recipe_data.instructions,
        comments=[],
        avgRating=None
    )

@app.get("/recipes/{recipeId}")
async def get_recipe(recipeId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('SELECT id, title, ingredients, instructions, avg_rating FROM recipes WHERE id = ?', (recipeId,))
    recipe_row = c.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_id, title, ingredients_json, instructions, avg_rating = recipe_row
    ingredients = json.loads(ingredients_json)
    c.execute('SELECT comment FROM comments WHERE recipe_id = ?', (recipeId,))
    comments = [{"comment": row[0]} for row in c.fetchall()]
    conn.close()
    
    html = recipe_detail_template.render(
        recipe={
            "id": recipe_id,
            "title": title,
            "ingredients": ingredients,
            "instructions": instructions,
            "comments": comments,
            "avgRating": avg_rating
        }
    )
    return HTMLResponse(content=html, status_code=200)

@app.post("/recipes/{recipeId}/comments")
async def add_comment(recipeId: str, comment_data: CommentCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('SELECT id FROM recipes WHERE id = ?', (recipeId,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    c.execute('INSERT INTO comments (recipe_id, comment) VALUES (?, ?)', (recipeId, comment_data.comment))
    conn.commit()
    conn.close()
    return {"message": "Comment added"}

@app.post("/recipes/{recipeId}/ratings")
async def add_rating(recipeId: str, rating_data: RatingCreate):
    rating = rating_data.rating
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('SELECT id FROM recipes WHERE id = ?', (recipeId,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    c.execute('INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)', (recipeId, rating))
    c.execute('SELECT AVG(rating) FROM ratings WHERE recipe_id = ?', (recipeId,))
    avg_rating = c.fetchone()[0]
    c.execute('UPDATE recipes SET avg_rating = ? WHERE id = ?', (avg_rating, recipeId))
    conn.commit()
    conn.close()
    return {"message": "Rating added"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)