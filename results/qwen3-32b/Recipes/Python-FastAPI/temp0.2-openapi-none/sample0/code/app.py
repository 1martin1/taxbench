import sqlite3
from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json

app = FastAPI()

class RecipeCreate(BaseModel):
    title: str
    ingredients: list[str]
    instructions: str

class CommentCreate(BaseModel):
    comment: str

class RatingCreate(BaseModel):
    rating: int

def get_db_connection():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            ingredients TEXT NOT NULL,
            instructions TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/recipes")
def get_recipes_overview():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes")
    recipes = cursor.fetchall()
    conn.close()
    html = "<html><body><h1>Recipes</h1><ul>"
    for recipe in recipes:
        html += f"<li><a href='/recipes/{recipe['id']}'>{recipe['title']}</a></li>"
    html += "</ul></body></html>"
    return HTMLResponse(content=html, status_code=200)

@app.post("/recipes/upload")
def upload_recipe(recipe: RecipeCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO recipes (title, ingredients, instructions) VALUES (?, ?, ?)",
        (recipe.title, json.dumps(recipe.ingredients), recipe.instructions)
    )
    recipe_id = cursor.lastrowid
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

@app.get("/recipes/{recipeId}", response_class=HTMLResponse)
def get_recipe(recipeId: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM recipes WHERE id = ?", (recipeId,))
    recipe_row = cursor.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments = [{"comment": row[0]} for row in cursor.fetchall()]
    cursor.execute("SELECT AVG(rating) FROM ratings WHERE recipe_id = ?", (recipeId,))
    avg_rating_row = cursor.fetchone()
    avg_rating = avg_rating_row[0]
    conn.close()
    recipe = {
        "id": recipe_row['id'],
        "title": recipe_row['title'],
        "ingredients": json.loads(recipe_row['ingredients']),
        "instructions": recipe_row['instructions'],
        "comments": comments,
        "avgRating": avg_rating if avg_rating is not None else None
    }
    html = f"""
    <html>
        <body>
            <h1>{recipe['title']}</h1>
            <h2>Ingredients</h2>
            <ul>
                {''.join(f"<li>{ing}</li>" for ing in recipe['ingredients'])}
            </ul>
            <h2>Instructions</h2>
            <p>{recipe['instructions']}</p>
            <h2>Comments</h2>
            <ul>
                {''.join(f"<li>{c['comment']}</li>" for c in recipe['comments'])}
            </ul>
            <h2>Average Rating</h2>
            <p>{recipe['avgRating'] if recipe['avgRating'] is not None else 'No ratings yet'}</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.post("/recipes/{recipeId}/comments")
def add_comment(recipeId: int, comment: CommentCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment.comment))
    conn.commit()
    conn.close()
    return {"status": "Comment added"}

@app.post("/recipes/{recipeId}/ratings")
def add_rating(recipeId: int, rating: RatingCreate):
    if not (1 <= rating.rating <= 5):
        raise HTTPException(status_code=400, detail="Invalid rating")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating.rating))
    conn.commit()
    conn.close()
    return {"status": "Rating added"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)