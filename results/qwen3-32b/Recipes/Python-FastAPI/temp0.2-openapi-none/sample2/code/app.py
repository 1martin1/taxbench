import sqlite3
import json
import uuid
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional

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
            instructions TEXT NOT NULL,
            comments TEXT NOT NULL,
            avg_rating REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    """)
    conn.commit()
    conn.close()

class RecipeCreate(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str

class CommentCreate(BaseModel):
    comment: str

class RatingCreate(BaseModel):
    rating: int

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

@app.post("/recipes/upload", status_code=201)
def upload_recipe(recipe: RecipeCreate):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    comments_json = json.dumps([])
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO recipes (id, title, ingredients, instructions, comments, avg_rating)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (recipe_id, recipe.title, ingredients_json, recipe.instructions, comments_json, None))
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
    cursor.execute("SELECT id, title, ingredients, instructions, comments, avg_rating FROM recipes WHERE id = ?", (recipeId,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_id, title, ingredients_json, instructions, comments_json, avg_rating = row
    ingredients = json.loads(ingredients_json)
    comments = json.loads(comments_json)
    html = f"<html><body><h1>{title}</h1><h2>Ingredients</h2><ul>"
    for ing in ingredients:
        html += f"<li>{ing}</li>"
    html += f"</ul><h2>Instructions</h2><p>{instructions}</p><h2>Comments</h2><ul>"
    for comment in comments:
        html += f"<li>{comment['comment']}</li>"
    html += f"</ul><h2>Average Rating</h2><p>{str(avg_rating) if avg_rating is not None else 'N/A'}</p></body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/recipes/{recipeId}/comments", status_code=201)
def add_comment(recipeId: str, comment_data: CommentCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT comments FROM recipes WHERE id = ?", (recipeId,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    comments_json = row[0]
    comments = json.loads(comments_json)
    new_comment = {"comment": comment_data.comment}
    comments.append(new_comment)
    new_comments_json = json.dumps(comments)
    cursor.execute("UPDATE recipes SET comments = ? WHERE id = ?", (new_comments_json, recipeId))
    conn.commit()
    conn.close()
    return Response(status_code=201)

@app.post("/recipes/{recipeId}/ratings", status_code=201)
def add_rating(recipeId: str, rating_data: RatingCreate):
    if not 1 <= rating_data.rating <= 5:
        raise HTTPException(status_code=400, detail="Invalid rating")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating_data.rating))
    cursor.execute("SELECT AVG(rating) FROM ratings WHERE recipe_id = ?", (recipeId,))
    avg = cursor.fetchone()[0]
    cursor.execute("UPDATE recipes SET avg_rating = ? WHERE id = ?", (avg, recipeId))
    conn.commit()
    conn.close()
    return Response(status_code=201)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)