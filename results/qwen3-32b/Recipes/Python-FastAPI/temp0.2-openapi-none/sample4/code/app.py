from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional, Dict
import sqlite3
import uuid
import json

app = FastAPI()

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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes (id)
        )
    """)
    conn.commit()
    conn.close()

class RecipeCreate(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str

class RecipeResponse(BaseModel):
    id: str
    title: str
    ingredients: List[str]
    instructions: str
    comments: List[Dict[str, str]]
    avgRating: Optional[float]

class CommentCreate(BaseModel):
    comment: str

class RatingCreate(BaseModel):
    rating: int

@app.post("/recipes/upload", response_model=RecipeResponse)
def upload_recipe(recipe: RecipeCreate):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO recipes (id, title, ingredients, instructions)
        VALUES (?, ?, ?, ?)
    """, (recipe_id, recipe.title, ingredients_json, recipe.instructions))
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

@app.get("/recipes")
def get_recipes_overview():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes")
    recipes = cursor.fetchall()
    conn.close()
    html = "<html><body><h1>Recipes</h1><ul>"
    for recipe_id, title in recipes:
        html += f"<li><a href='/recipes/{recipe_id}'>{title}</a></li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

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
        total = sum(r[0] for r in ratings)
        avg_rating = total / len(ratings)
    conn.close()
    html = f"<html><body><h1>{title}</h1><h2>Ingredients</h2><ul>"
    for ingredient in ingredients:
        html += f"<li>{ingredient}</li>"
    html += f"</ul><h2>Instructions</h2><p>{instructions}</p><h2>Comments</h2><ul>"
    for comment in comments:
        html += f"<li>{comment['comment']}</li>"
    html += f"</ul><h2>Average Rating</h2><p>{avg_rating if avg_rating is not None else 'No ratings yet'}</p></body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/recipes/{recipeId}/comments")
def add_comment(recipeId: str, comment: CommentCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("""
        INSERT INTO comments (recipe_id, comment)
        VALUES (?, ?)
    """, (recipeId, comment.comment))
    conn.commit()
    conn.close()
    return Response(status_code=201)

@app.post("/recipes/{recipeId}/ratings")
def add_rating(recipeId: str, rating: RatingCreate):
    if not 1 <= rating.rating <= 5:
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("""
        INSERT INTO ratings (recipe_id, rating)
        VALUES (?, ?)
    """, (recipeId, rating.rating))
    conn.commit()
    conn.close()
    return Response(status_code=201)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)