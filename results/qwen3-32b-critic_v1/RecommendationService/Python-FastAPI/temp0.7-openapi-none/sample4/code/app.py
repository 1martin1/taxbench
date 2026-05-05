import sqlite3
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, field_validator
from starlette.responses import HTMLResponse
import os

app = FastAPI()

class ProductRequest(BaseModel):
    product_name: str
    tags: list[str]

    @field_validator('product_name')
    def validate_product_name_length(cls, value):
        if len(value) > 255:
            raise ValueError("Product name exceeds maximum length of 255 characters")
        return value

    @field_validator('tags')
    def validate_tags_count(cls, value):
        if len(value) > 50:
            raise ValueError("Too many tags (maximum 50)")
        return value

    @field_validator('tags')
    def validate_tag_content(cls, value):
        for tag in value:
            stripped_tag = tag.strip()
            if not stripped_tag:
                raise ValueError("Tag cannot be empty after stripping whitespace")
            if len(stripped_tag) > 100:
                raise ValueError("Tag exceeds maximum length of 100 characters")
        return value

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, product_name TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS product_tags 
                 (product_id INTEGER NOT NULL, tag TEXT NOT NULL, 
                  FOREIGN KEY (product_id) REFERENCES products(id))''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/recommender")
async def post_product(product: ProductRequest):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (product_name) VALUES (?)", (product.product_name,))
        product_id = c.lastrowid
        for tag in product.tags:
            normalized_tag = tag.strip().lower()
            c.execute("INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", 
                     (product_id, normalized_tag))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

@app.get("/recommender")
async def get_products(tags: str):
    raw_tags = [t.strip() for t in tags.split(',') if t.strip()]
    tag_list = [t.lower() for t in raw_tags]
    
    if len(tag_list) > 100:
        return HTMLResponse("Too many tags provided (maximum 100)", status_code=400)
    
    for tag in tag_list:
        if len(tag) > 100:
            return HTMLResponse("A tag exceeds maximum length of 100 characters", status_code=400)
    
    if not tag_list:
        return HTMLResponse("<html><body>No valid tags provided</body></html>", status_code=400)
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    query = """
        SELECT DISTINCT products.id, products.product_name 
        FROM products 
        JOIN product_tags ON products.id = product_tags.product_id 
        WHERE product_tags.tag IN ({})
        LIMIT 100
    """.format(','.join('?' * len(tag_list)))
    
    c.execute(query, tag_list)
    results = c.fetchall()
    conn.close()
    
    html_content = "<html><body><h1>Recommended Products</h1><ul>"
    for product_id, product_name in results:
        html_content += f"<li><a href='/product/{product_id}'>{product_name}</a></li>"
    html_content += "</ul></body></html>"
    
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)