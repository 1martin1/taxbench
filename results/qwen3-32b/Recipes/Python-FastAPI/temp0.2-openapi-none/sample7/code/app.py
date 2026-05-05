from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import sqlite3
import uuid
import json
from starlette.responses import HTMLResponse

app = FastAPI()

# Initialize database on startup
@app.on_event("startup")
def on_startup():
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
    init_db()

class RecipeBase(BaseModel):
    title: str
    ingredients: list[str]
    instructions: str

class RecipeCreate(RecipeBase):
    pass

class RecipeOut(RecipeBase):
    id: str
    comments: list[dict[str, str]]
    avgRating: float | None

@app.post("/recipes/upload", status_code=201, response_model=RecipeOut)
def upload_recipe(recipe: RecipeCreate):
    recipe_id = str(uuid.uuid4())
    ingredients_json = json.dumps(recipe.ingredients)
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("INSERT INTO recipes (id, title, ingredients, instructions) VALUES (?, ?, ?, ?)",
              (recipe_id, recipe.title, ingredients_json, recipe.instructions))
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
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, title FROM recipes")
    recipes = c.fetchall()
    conn.close()
    html = "<html><body><h1>Recipes</h1><ul>"
    for recipe_id, title in recipes:
        html += f"<li><a href='/recipes/{recipe_id}'>{title}</a></li>"
    html += "</ul></body></html>"
    return HTMLResponse(content=html, status_code=200)

@app.get("/recipes/{recipeId}")
def get_recipe(recipeId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, title, ingredients, instructions FROM recipes WHERE id = ?", (recipeId,))
    recipe_row = c.fetchone()
    if not recipe_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_id, title, ingredients_json, instructions = recipe_row
    ingredients = json.loads(ingredients_json)
    c.execute("SELECT comment FROM comments WHERE recipe_id = ?", (recipeId,))
    comments = [{"comment": row[0]} for row in c.fetchall()]
    c.execute("SELECT rating FROM ratings WHERE recipe_id = ?", (recipeId,))
    ratings = [row[0] for row in c.fetchall()]
    avg_rating = sum(ratings)/len(ratings) if ratings else None
    conn.close()
    html = f"<html><body><h1>{title}</h1><h2>Ingredients</h2><ul>"
    for ingredient in ingredients:
        html += f"<li>{ingredient}</li>"
    html += f"</ul><h2>Instructions</h2><p>{instructions}</p><h2>Comments</h2><ul>"
    for comment in comments:
        html += f"<li>{comment['comment']}</li>"
    html += "</ul>"
    if avg_rating is not None:
        html += f"<h2>Average Rating: {avg_rating:.1f}</h2>"
    html += "</body></html>"
    return HTMLResponse(content=html, status_code=200)

class CommentCreate(BaseModel):
    comment: str

@app.post("/recipes/{recipeId}/comments")
def add_comment(recipeId: str, comment: CommentCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    c.execute("INSERT INTO comments (recipe_id, comment) VALUES (?, ?)", (recipeId, comment.comment))
    conn.commit()
    conn.close()
    return {"status": "Comment added successfully"}

class RatingCreate(BaseModel):
    rating: int

@app.post("/recipes/{recipeId}/ratings")
def add_rating(recipeId: str, rating: RatingCreate):
    if not 1 <= rating.rating <= 5:
        raise HTTPException(status_code=400, detail="Invalid input")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id FROM recipes WHERE id = ?", (recipeId,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Recipe not found")
    c.execute("INSERT INTO ratings (recipe_id, rating) VALUES (?, ?)", (recipeId, rating.rating))
    conn.commit()
    conn.close()
    return {"status": "Rating added successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)