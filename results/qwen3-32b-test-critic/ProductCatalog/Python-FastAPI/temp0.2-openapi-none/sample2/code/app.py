import sqlite3
import json
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pydantic.config import ConfigDict
from typing import Dict, Any, Optional

app = FastAPI()

# Initialize the database
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Helper function to escape LIKE special characters
def escape_like(query: str) -> str:
    return (query.replace('\\', '\\\\')
             .replace('%', '\\%')
             .replace('_', '\\_'))

# Pydantic models
class ProductAdd(BaseModel):
    name: str
    model_config = ConfigDict(extra = 'allow')

class ProductUpload(BaseModel):
    name: str
    data: str

@app.post("/add", status_code=201)
async def add_product(product: ProductAdd):
    additional_props = product.model_dump(exclude={'name'})
    for key, value in additional_props.items():
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Property {key} is not a string")
    data_str = json.dumps(additional_props)
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (name, data) VALUES (?, ?)", (product.name, data_str))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Product name already exists")
    finally:
        conn.close()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str = Query(...)):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    data_str = row[0]
    return {"data": data_str}

@app.post("/upload")
async def upload_product(product_upload: ProductUpload):
    data_str = product_upload.data
    try:
        data_dict = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    if not isinstance(data_dict, dict):
        raise HTTPException(status_code=400, detail="Data must be a JSON object")
    for key, value in data_dict.items():
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Property {key} is not a string")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("REPLACE INTO products (name, data) VALUES (?, ?)", (product_upload.name, data_str))
    conn.commit()
    conn.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str = Query(...)):
    escaped_query = escape_like(query)
    pattern = f'%{escaped_query}%'
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        SELECT name, data FROM products
        WHERE name LIKE ? ESCAPE '\'
        OR EXISTS (
            SELECT 1 FROM JSON_EACH(data)
            WHERE value LIKE ? ESCAPE '\'
        )
    ''', (pattern, pattern))
    rows = c.fetchall()
    conn.close()
    results = []
    for name, data_str in rows:
        try:
            data_dict = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        product = {'name': name}
        product.update(data_dict)
        results.append(product)
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)