from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import sqlite3
from html import escape

app = FastAPI()

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
            product_name TEXT UNIQUE NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name TEXT UNIQUE NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS product_tags (
            product_id INTEGER,
            tag_id INTEGER,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (tag_id) REFERENCES tags(id),
            PRIMARY KEY (product_id, tag_id)
        )
    """)
    db.commit()
    db.close()

class ProductCreate(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductCreate):
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO products (product_name) VALUES (?) ON CONFLICT (product_name) DO NOTHING", 
                           (product.product_name,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=400, detail="Product already exists")
        product_id = db.lastrowid
        
        for tag in product.tags:
            db.execute("INSERT OR IGNORE INTO tags (tag_name) VALUES (?)", (tag,))
            cursor = db.execute("SELECT id FROM tags WHERE tag_name = ?", (tag,))
            tag_row = cursor.fetchone()
            if not tag_row:
                raise HTTPException(status_code=500, detail="Database error inserting tag")
            tag_id = tag_row['id']
            
            db.execute("INSERT OR IGNORE INTO product_tags (product_id, tag_id) VALUES (?, ?)", 
                      (product_id, tag_id))
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()
    return {"status": "success"}

@app.get("/recommender", response_class=HTMLResponse)
async def get_products(tags: str):
    tag_list = [t.strip() for t in tags.split(',')]
    if len(tag_list) > 100:
        raise HTTPException(status_code=400, detail="Too many tags (max 100 allowed)")
    
    db = get_db()
    try:
        placeholders = ','.join('?' * len(tag_list))
        cursor = db.execute(f"SELECT tag_name, id FROM tags WHERE tag_name IN ({placeholders})", tag_list)
        tag_dict = {row['tag_name']: row['id'] for row in cursor.fetchall()}
        
        if not tag_dict:
            return "<html><body><h1>No products found</h1></body></html>"
        
        tag_ids = list(tag_dict.values())
        placeholders_tags = ','.join('?' * len(tag_ids))
        query = f"""
            SELECT p.product_name 
            FROM products p
            JOIN product_tags pt ON p.id = pt.product_id
            WHERE pt.tag_id IN ({placeholders_tags})
            GROUP BY p.product_name
        """
        cursor = db.execute(query, tag_ids)
        products = [row['product_name'] for row in cursor.fetchall()]
        
        html = "<html><body><h1>Recommendations</h1><ul>"
        for product in products:
            html += f"<li><a href='/product/{escape(product)}'>{escape(product)}</a></li>"
        html += "</ul></body></html>"
        return html
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)