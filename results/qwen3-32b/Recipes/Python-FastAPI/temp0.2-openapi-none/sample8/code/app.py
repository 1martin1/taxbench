import sqlite3
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import uuid
import json
import uvicorn

app = FastAPI()

def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
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
                FOREIGN KEY (recipe_id) REFERENCES recipes (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id TEXT NOT NULL,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                FOREIGN KEY (recipe_id) REFERENCES recipes (id)
            )
        ''')
        conn.commit()

init_db()

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

def get_recipe_by_id(recipe_id: str):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
        recipe_row = cursor.fetchone()
        if not recipe_row:
            raise HTTPException(status_code=404, detail="Recipe not found")
        cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipe_id,))
        comments = cursor.fetchall()
        comments_list = [{"comment": c["comment"]} for c in comments]
        cursor.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipe_id,))
        ratings = cursor.fetchall()
        avg_rating = None
        if ratings:
            avg_rating = sum(r["rating"] for r in ratings) / len(ratings)
        return {
            "id": recipe_row["id"],
            "title": recipe_row["title"],
            "ingredients": json.loads(recipe_row["ingredients"]),
            "instructions": recipe_row["instructions"],
            "comments": comments_list,
            "avgRating": avg_rating
        }

@app.get("/recipes")
async def get_recipes_overview():
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, title FROM recipes")
        recipes = cursor.fetchall()
        html_content = "<html><body><h1>Recipes</h1><ul>"
        for recipe in recipes:
            html_content += f"<li><a href='/recipes/{recipe['id']}'>{recipe['title']}</a></li>"
        html_content += "</ul></body></html>"
        return HTMLResponse(content=html_content, status_code=200)

@app.post("/recipes/upload", status_code=201, response_model=RecipeResponse)
async def upload_recipe(recipe_data: RecipeCreate):
    recipe_id = str(uuid.uuid4().hex)
    ingredients_json = json.dumps(recipe_data.ingredients)
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO recipes (id, title, ingredients, instructions, avg_rating)
            VALUES (?, ?, ?, ?, ?)
        ''', (recipe_id, recipe_data.title, ingredients_json, recipe_data.instructions, None))
        conn.commit()
    recipe = get_recipe_by_id(recipe_id)
    return recipe

@app.get("/recipes/{recipeId}")
async def get_recipe(recipeId: str):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM recipes WHERE id = ?", (recipeId,))
        recipe_row = cursor.fetchone()
        if not recipe_row:
            raise HTTPException(status_code=404, detail="Recipe not found")
        cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
        comments = cursor.fetchall()
        comments_html = "<ul>"
        for c in comments:
            comments_html += f"<li>{c['comment']}</li>"
        comments_html += "</ul>"
        cursor.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipeId,))
        ratings = cursor.fetchall()
        avg_rating = None
        if ratings:
            avg_rating = sum(r["rating"] for r in ratings) / len(ratings)
        html_content = f"""
        <html>
            <body>
                <h1>{recipe_row['title']}</h1>
                <h2>Ingredients</h2>
                <ul>
                    {"".join(f"<li>{ing}</li>" for ing in json.loads(recipe_row['ingredients']))}
                </ul>
                <h2>Instructions</h2>
                <p>{recipe_row['instructions']}</p>
                <h2>Comments</h2>
                {comments_html}
                <h2>Average Rating</h2>
                <p>{avg_rating if avg_rating is not None else 'No ratings yet'}</p>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)

@app.post("/recipes/{recipeId}/comments", status_code=201)
async def add_comment(recipeId: str, comment_data: CommentCreate):
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Recipe not found")
        cursor.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment_data.comment))
        conn.commit()
    return Response(status_code=201)

@app.post("/recipes/{recipeId}/ratings", status_code=201)
async def add_rating(recipeId: str, rating_data: RatingCreate):
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Recipe not found")
            cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating_data.rating))
            cursor.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipeId,))
            ratings = cursor.fetchall()
            total = sum(r[0] for r in ratings)
            avg = total / len(ratings)
            cursor.execute("UPDATE recipes SET avg_rating = ? WHERE id = ?", (avg, recipeId))
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid rating value")
    return Response(status_code=201)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)