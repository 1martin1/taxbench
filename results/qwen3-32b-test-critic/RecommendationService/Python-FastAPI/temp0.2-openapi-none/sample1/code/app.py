import sqlite3
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List
import uvicorn
import html

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL UNIQUE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name TEXT NOT NULL UNIQUE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_tags (
            product_id INTEGER,
            tag_id INTEGER,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (tag_id) REFERENCES tags(id),
            PRIMARY KEY (product_id, tag_id)
        )
    ''')
    conn.commit()
    conn.close()

app.add_event_handler("startup", init_db)

class ProductModel(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductModel):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (product_name) VALUES (?)", (product.product_name,))
        product_id = c.lastrowid
        for tag in product.tags:
            c.execute("INSERT OR IGNORE INTO tags (tag_name) VALUES (?)", (tag,))
            c.execute("SELECT id FROM tags WHERE tag_name = ?", (tag,))
            tag_id = c.fetchone()[0]
            c.execute("INSERT INTO product_tags (product_id, tag_id) VALUES (?, ?)", (product_id, tag_id))
        conn.commit()
        return Response(status_code=200)
    except sqlite3.IntegrityError as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input")
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()

@app.get("/recommender")
async def get_products(tags: str):
    tag_list = [t.strip() for t in tags.split(',')]
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    placeholders = ','.join('?' * len(tag_list))
    c.execute(f"SELECT id, tag_name FROM tags WHERE tag_name IN ({placeholders})", tag_list)
    existing_tags = c.fetchall()
    if not existing_tags:
        conn.close()
        return Response(content="<html><body><h1>No products found</h1></body></html>", media_type="text/html")
    tag_ids = [tag_id for tag_id, tag_name in existing_tags]
    placeholders_tag_ids = ','.join('?' * len(tag_ids))
    c.execute(f'''
        SELECT p.id, p.product_name 
        FROM products p
        JOIN product_tags pt ON p.id = pt.product_id
        WHERE pt.tag_id IN ({placeholders_tag_ids})
        GROUP BY p.id
    ''', tag_ids)
    products = c.fetchall()
    conn.close()
    html = "<html><body><h1>Products</h1><ul>"
    for product_id, product_name in products:
        html += f"<li><a href='/product/{product_id}'>{html.escape(product_name)}</a></li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)