import sqlite3
import uuid
import json
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from fastapi.responses import Response

app = FastAPI()

def get_db():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS recipes
                        (id TEXT PRIMARY KEY,
                         title TEXT NOT NULL,
                         ingredients TEXT NOT NULL,
                         instructions TEXT NOT NULL,
                         avg_rating REAL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS comments
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         recipe_id TEXT NOT NULL,
                         comment TEXT NOT NULL,
                         FOREIGN KEY (recipe_id) REFERENCES recipes(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ratings
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         recipe_id TEXT NOT NULL,
                         rating INTEGER NOT NULL,
                         FOREIGN KEY (recipe_id) REFERENCES recipes(id))''')
        conn.commit()

init_db()

class UploadRecipeRequest(BaseModel):
    title: str
    ingredients: List[str]
    instructions: str

@app.get("/recipes")
def get_recipes_overview():
    conn = get_db()
    try:
        recipes = conn.execute("SELECT id, title FROM recipes").fetchall()
        html = "<html><body><h1>Recipe Overview</h1><ul>"
        for recipe in recipes:
            html += f"<li><a href='/recipes/{recipe['id']}'>{recipe['title']}</a></li>"
        html += "</ul></body></html>"
        return Response(content=html, media_type="text/html")
    except Exception:
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        conn.close()

@app.post("/recipes/upload", status_code=201)
def upload_recipe(recipe: UploadRecipeRequest):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    conn = get_db()
    try:
        conn.execute("INSERT INTO recipes (id, title, ingredients, instructions, avg_rating) VALUES (?, ?, ?, ?, ?)",
                     (recipe_id, recipe.title, ingredients_json, recipe.instructions, None))
        conn.commit()
        return {
            "id": recipe_id,
            "title": recipe.title,
            "ingredients": recipe.ingredients,
            "instructions": recipe.instructions,
            "comments": [],
            "avgRating": None
        }
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()

@app.get("/recipes/{recipeId}")
def get_recipe(recipeId: str = Path(..., description="The ID of the recipe")):
    conn = get_db()
    try:
        recipe_row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipeId,)).fetchone()
        if not recipe_row:
            raise HTTPException(status_code=404, detail="Recipe not found")
        ingredients = json.loads(recipe_row['ingredients'])
        comments = conn.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,)).fetchall()
        comments_list = [{"comment": c["comment"]} for c in comments]
        html = f"<html><body><h1>{recipe_row['title']}</h1><h2>Ingredients</h2><ul>"
        for ingredient in ingredients:
            html += f"<li>{ingredient}</li>"
        html += "</ul><h2>Instructions</h2><p>" + recipe_row['instructions'] + "</p>"
        html += "<h2>Comments</h2><ul>"
        for comment in comments_list:
            html += f"<li>{comment['comment']}</li>"
        html += "</ul>"
        avg_rating = recipe_row['avg_rating']
        if avg_rating is not None:
            html += f"<h2>Average Rating: {avg_rating:.1f}</h2>"
        else:
            html += "<h2>No ratings yet</h2>"
        html += "</body></html>"
        return Response(content=html, media_type="text/html")
    except Exception:
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        conn.close()

@app.post("/recipes/{recipeId}/comments")
def add_comment(recipeId: str, comment_data: dict):
    comment = comment_data.get("comment")
    if not comment or not isinstance(comment, str):
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = get_db()
    try:
        recipe_exists = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipeId,)).fetchone()
        if not recipe_exists:
            raise HTTPException(status_code=404, detail="Recipe not found")
        conn.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment))
        conn.commit()
        return Response(status_code=201)
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()

@app.post("/recipes/{recipeId}/ratings")
def add_rating(recipeId: str, rating_data: dict):
    rating = rating_data.get("rating")
    if not isinstance(rating, int) or not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = get_db()
    try:
        recipe_exists = conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipeId,)).fetchone()
        if not recipe_exists:
            raise HTTPException(status_code=404, detail="Recipe not found")
        conn.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating))
        avg_row = conn.execute("SELECT AVG(rating) as avg FROM ratings WHERE recipe_id = ?", (recipeId,)).fetchone()
        avg_rating = avg_row['avg']
        conn.execute("UPDATE recipes SET avg_rating = ? WHERE id = ?", (avg_rating, recipeId))
        conn.commit()
        return Response(status_code=201)
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)