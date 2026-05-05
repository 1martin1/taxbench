import sqlite3
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List
import uvicorn

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products
                 (id INTEGER PRIMARY KEY, product_name TEXT UNIQUE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS product_tags
                 (product_id INTEGER, tag TEXT, FOREIGN KEY(product_id) REFERENCES products(id))''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class ProductCreate(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (product_name) VALUES (?)", (product.product_name,))
        product_id = c.lastrowid
        for tag in product.tags:
            c.execute("INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", (product_id, tag))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product name already exists")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return Response(status_code=200)

@app.get("/recommender")
async def get_products(tags: str):
    tag_list = [t.strip() for t in tags.split(',')]
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    placeholders = ','.join('?' * len(tag_list))
    query = f"""
    SELECT DISTINCT products.product_name
    FROM products
    JOIN product_tags ON products.id = product_tags.product_id
    WHERE product_tags.tag IN ({placeholders})
    """
    try:
        c.execute(query, tag_list)
        products = c.fetchall()
    finally:
        conn.close()
    html = "<html><body><ul>"
    for product in products:
        html += f"<li><a href='#{product[0]}'>{product[0]}</a></li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)