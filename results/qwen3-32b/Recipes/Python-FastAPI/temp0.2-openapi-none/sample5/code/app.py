import sqlite3
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional
import uuid
import json
import uvicorn
import html

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS recipes
                 (id TEXT PRIMARY KEY, title TEXT, ingredients TEXT, instructions TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS comments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT, comment TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ratings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT, rating INTEGER)''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class CreateRecipe(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str

class Comment(BaseModel):
    comment: str

class Rating(BaseModel):
    rating: int

@app.get("/recipes")
async def get_recipes():
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute("SELECT id, title FROM recipes")
        recipes = c.fetchall()
        html = "<html><body><h1>Recent and Top Recipes</h1><ul>"
        for recipe_id, title in recipes:
            html += f"<li><a href='/recipes/{recipe_id}'>{html.escape(title)}</a></li>"
        html += "</ul></body></html>"
        return Response(content=html, media_type="text/html")
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        if conn:
            conn.close()

@app.post("/recipes/upload", status_code=201)
async def upload_recipe(recipe: CreateRecipe):
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        recipe_id = str(uuid.uuid4())
        ingredients_json = json.dumps(recipe.ingredients)
        c.execute("INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
                  (recipe_id, recipe.title, ingredients_json, recipe.instructions))
        conn.commit()
        return {
            "id": recipe_id,
            "title": recipe.title,
            "ingredients": recipe.ingredients,
            "instructions": recipe.instructions,
            "comments": [],
            "avgRating": None
        }
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        if conn:
            conn.close()

@app.get("/recipes/{recipeId}")
async def get_recipe(recipeId: str):
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute("SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipeId,))
        recipe_row = c.fetchone()
        if not recipe_row:
            raise HTTPException(status_code=404, detail="Recipe not found")
        recipe_id, title, ingredients_json, instructions = recipe_row
        ingredients = json.loads(ingredients_json)

        c.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
        comments = [{"comment": row[0]} for row in c.fetchall()]

        c.execute("SELECT AVG(rating) FROM ratings WHERE recipe_id = ?", (recipeId,))
        avg_rating_row = c.fetchone()
        avg_rating = avg_rating_row[0]
        if avg_rating is None:
            avg_rating = None
        else:
            avg_rating = float(avg_rating)

        html = f"<html><body><h1>{html.escape(title)}</h1><h2>Ingredients</h2><ul>"
        for ingredient in ingredients:
            html += f"<li>{html.escape(ingredient)}</li>"
        html += f"</ul><h2>Instructions</h2><p>{html.escape(instructions)}</p><h2>Comments</h2><ul>"
        for comment in comments:
            html += f"<li>{html.escape(comment['comment'])}</li>"
        html += f"</ul><h2>Average Rating</h2><p>{avg_rating if avg_rating is not None else 'No ratings yet'}</p></body></html>"

        return Response(content=html, media_type="text/html")
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        if conn:
            conn.close()

@app.post("/recipes/{recipeId}/comments")
async def add_comment(recipeId: str, comment: Comment):
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Recipe not found")
        c.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment.comment))
        conn.commit()
        return Response(status_code=201)
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        if conn:
            conn.close()

@app.post("/recipes/{recipeId}/ratings")
async def add_rating(recipeId: str, rating: Rating):
    if not 1 <= rating.rating <= 5:
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Recipe not found")
        c.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating.rating))
        conn.commit()
        return Response(status_code=201)
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)