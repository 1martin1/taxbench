import sqlite3
import html
from fastapi import FastAPI, Query, HTTPException, Response
from pydantic import BaseModel
from typing import List
import json
import os

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

app.add_event_handler("startup", init_db)

class ProductCreate(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductCreate):
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        tags_json = json.dumps(product.tags)
        c.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)", 
                  (product.product_name, tags_json))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return Response(status_code=200)

@app.get("/recommender")
async def get_products(tags: str = Query(..., description="Tags to search for separated by comma.")):
    tag_list = [t.strip() for t in tags.split(',')]
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT product_name, tags FROM products")
    rows = c.fetchall()
    conn.close()

    matching_products = []
    for product_name, tags_json in rows:
        tags = json.loads(tags_json)
        if any(tag in tag_list for tag in tags):
            matching_products.append(product_name)

    html_content = "<html><body><h1>Recommended Products</h1><ul>"
    for product in matching_products:
        safe_product = html.escape(product)
        html_content += f"<li><a href='#{safe_product}'>{safe_product}</a></li>"
    html_content += "</ul></body></html>"

    return Response(content=html_content, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)