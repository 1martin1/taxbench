from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
import sqlite3
import uuid
import json
import html
from typing import List, Optional, Dict
from fastapi.responses import Response

app = FastAPI()

class RecipeCreate(BaseModel):
    title: str = Field(..., max_length=200)
    ingredients: List[str] = Field(..., max_length=50)
    instructions: str = Field(..., max_length=1000)

    @field_validator('ingredients')
    def validate_ingredients(cls, v):
        for ingredient in v:
            if len(ingredient) > 100:
                raise ValueError('Ingredient too long')
        return v

class RecipeOut(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[Dict[str, str]]
    avgRating: Optional[float]

class CommentCreate(BaseModel):
    comment: str = Field(..., max_length=500)

class RatingCreate(BaseModel):
    rating: int

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes (id)
        )
    """)
    conn.commit()
    conn.close()

@app.get("/recipes")
def get_recipes_overview():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes")
    recipes = cursor.fetchall()
    conn.close()

    html = "<html><body><h1>Recent Recipes</h1><ul>"
    for recipe_id, title in recipes:
        html += f"<li><a href='/recipes/{recipe_id}'>{html.escape(title)}</a></li>"
    html += "</ul></body></html>"
    return html

@app.post("/recipes/upload", response_model=RecipeOut)
def upload_recipe(recipe: RecipeCreate):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
        (recipe_id, recipe.title, ingredients_json, recipe.instructions)
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
def get_recipe(recipeId: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    cursor.execute("SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipeId,))
    recipe_row = cursor.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")

    recipe_id, title, ingredients_json, instructions = recipe_row
    ingredients = json.loads(ingredients_json)

    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments = [{"comment": row[0]} for row in cursor.fetchall()]

    cursor.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipeId,))
    ratings = cursor.fetchall()
    avg_rating = None
    if ratings:
        ratings_values = [r[0] for r in ratings]
        avg_rating = sum(ratings_values) / len(ratings_values)

    conn.close()

    html = f"<html><body><h1>{html.escape(title)}</h1>"
    html += "<h2>Ingredients</h2><ul>"
    for ingredient in ingredients:
        html += f"<li>{html.escape(ingredient)}</li>"
    html += "</ul>"

    html += f"<h2>Instructions</h2><p>{html.escape(instructions)}</p>"

    if avg_rating is not None:
        html += f"<h2>Average Rating: {avg_rating:.2f}</h2>"
    else:
        html += "<h2>No ratings yet.</h2>"

    html += "<h2>Comments</h2><ul>"
    for comment in comments:
        html += f"<li>{html.escape(comment['comment'])}</li>"
    html += "</ul></body></html>"

    return html

@app.post("/recipes/{recipeId}/comments")
def add_comment(recipeId: str, comment: CommentCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")

    cursor.execute(
        "INSERT INTO comments (recipe_id, comment) VALUES (?, ?)",
        (recipeId, comment.comment)
    )
    conn.commit()
    conn.close()

    return Response(status_code=201)

@app.post("/recipes/{recipeId}/ratings")
def add_rating(recipeId: str, rating: RatingCreate):
    if not (1 <= rating.rating <= 5):
        raise HTTPException(status_code=400, detail="Invalid input")

    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")

    cursor.execute(
        "INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)",
        (recipeId, rating.rating)
    )
    conn.commit()
    conn.close()

    return Response(status_code=201)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)