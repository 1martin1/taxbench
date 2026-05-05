from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel
from typing import List
import sqlite3
import json
import uvicorn
from html import escape

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            tags TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup():
    init_db()

class ProductCreate(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    tags_json = json.dumps(product.tags)
    c.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)", 
              (product.product_name, tags_json))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/recommender")
async def get_products(tags: str):
    # Validate input size
    if len(tags) > 1024:
        raise HTTPException(status_code=400, detail="Tags string too long")
    
    # Split and validate tags
    tag_list = [t.strip() for t in tags.split(',')]
    if not tag_list or len(tag_list) > 100:
        raise HTTPException(status_code=400, detail="Invalid tags")
    
    # Build SQL query with parameterized LIKE conditions
    placeholders = ' OR '.join(['tags LIKE ?'] * len(tag_list))
    query = f"SELECT product_name FROM products WHERE {placeholders}"
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    params = ['%' + tag + '%' for tag in tag_list]
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    # Limit results and generate HTML with escaped product names
    max_results = 100
    product_names = [row[0] for row in rows[:max_results]]
    
    html_content = "<html><body><ul>"
    for product in product_names:
        safe_product = escape(product)
        html_content += f"<li><a href='/product/{safe_product}'>{safe_product}</a></li>"
    html_content += "</ul></body></html>"
    
    return Response(content=html_content, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)