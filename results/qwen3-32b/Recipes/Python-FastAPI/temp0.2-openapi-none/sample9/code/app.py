from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import HTMLResponse
import sqlite3
import json
import uuid

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
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
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
            FOREIGN KEY (recipe_id) REFERENCES recipes (id)
        )
    """)
    conn.commit()
    conn.close()

class RecipeCreate:
    def __init__(self, title: str, ingredients: list[str], instructions: str):
        self.title = title
        self.ingredients = ingredients
        self.instructions = instructions

class Recipe:
    def __init__(self, title: str, ingredients: list[str], instructions: str, id: str, comments: list = [], avgRating: float | None = None):
        self.title = title
        self.ingredients = ingredients
        self.instructions = instructions
        self.id = id
        self.comments = comments
        self.avgRating = avgRating

@app.get("/recipes")
async def get_recipes_overview():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM recipes")
    rows = cursor.fetchall()
    conn.close()
    recipe_links = ''.join([f'<li><a href="/recipes/{id}">{title}</a></li>' for id, title in rows])
    html = f"""
    <html>
        <body>
            <h1>Recipe Overview</h1>
            <ul>{recipe_links}</ul>
        </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.post("/recipes/upload")
async def upload_recipe(recipe: dict):
    title = recipe.get("title")
    ingredients = recipe.get("ingredients")
    instructions = recipe.get("instructions")
    
    if not title or not ingredients or not instructions:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    recipe_id = str(uuid.uuid4())
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    ingredients_json = json.dumps(ingredients)
    cursor.execute("INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
                   (recipe_id, title, ingredients_json, instructions))
    conn.commit()
    conn.close()
    
    return {
        "id": recipe_id,
        "title": title,
        "ingredients": ingredients,
        "instructions": instructions,
        "comments": [],
        "avgRating": None
    }

@app.get("/recipes/{recipeId}")
async def get_recipe(recipeId: str):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipeId,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    id, title, ingredients_json, instructions = row
    ingredients = json.loads(ingredients_json)
    cursor.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments = cursor.fetchall()
    comments_list = [{"comment": comment[0]} for comment in comments]
    cursor.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipeId,))
    ratings = cursor.fetchall()
    avg_rating = None
    if ratings:
        ratings_values = [r[0] for r in ratings]
        avg_rating = sum(ratings_values) / len(ratings_values)
    conn.close()
    
    comments_html = ''.join([f'<p>{comment["comment"]}</p>' for comment in comments_list])
    html = f"""
    <html>
        <body>
            <h1>{title}</h1>
            <h2>Ingredients</h2>
            <ul>{"".join([f"<li>{ing}</li>" for ing in ingredients])}</ul>
            <h2>Instructions</h2>
            <p>{instructions}</p>
            <h2>Comments</h2>
            {comments_html}
            <h2>Average Rating</h2>
            <p>{avg_rating if avg_rating is not None else "No ratings yet"}</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.post("/recipes/{recipeId}/comments")
async def add_comment(recipeId: str, comment_data: dict):
    comment = comment_data.get("comment")
    if not comment:
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment))
    conn.commit()
    conn.close()
    return {"status": "Comment added"}

@app.post("/recipes/{recipeId}/ratings")
async def add_rating(recipeId: str, rating_data: dict):
    rating = rating_data.get("rating")
    if not isinstance(rating, int) or rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    cursor.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating))
    conn.commit()
    conn.close()
    return {"status": "Rating added"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)