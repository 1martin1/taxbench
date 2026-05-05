import sqlite3
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, model_validator
from typing import List
from fastapi.responses import HTMLResponse
import os

app = FastAPI()

def escape_tag(tag):
    return tag.replace('!', '!!').replace('%', '!%').replace('_', '!_')

def escape_html(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;')

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

@app.on_event("startup")
def create_tables():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            tags TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()

class ProductCreate(BaseModel):
    product_name: str
    tags: List[str]

    @model_validator(mode='after')
    def check_tags_not_empty(self):
        if not self.tags:
            raise ValueError('Tags list cannot be empty')
        return self

@app.post("/recommender")
async def post_product(product: ProductCreate):
    db = get_db()
    try:
        tags_str = ','.join(product.tags)
        db.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)",
                   (product.product_name, tags_str))
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        db.close()
    return {"status": "success"}

@app.get("/recommender")
async def get_products(tags: str = Query(...)):
    search_tags = [tag.strip() for tag in tags.split(',')]
    db = get_db()
    try:
        placeholders = ' OR '.join([f'tags LIKE ? ESCAPE '!''] * len(search_tags))
        query = f"SELECT * FROM products WHERE {placeholders}"
        params = [f',{escape_tag(tag)}' for tag in search_tags]
        cursor = db.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        db.close()

    html_content = "<html><body><h1>Products</h1><ul>"
    for row in rows:
        product_name = escape_html(row['product_name'])
        product_id = row['id']
        html_content += f"<li><a href='/product/{product_id}'>{product_name}</a></li>"
    html_content += "</ul></body></html>"

    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000)