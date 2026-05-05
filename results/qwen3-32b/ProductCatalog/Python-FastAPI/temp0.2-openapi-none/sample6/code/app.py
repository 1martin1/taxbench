import sqlite3
from fastapi import FastAPI, HTTPException, Query
import json
from pydantic import BaseModel, ConfigDict
from typing import Dict, Any, Optional

app = FastAPI()

def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS products (
                name TEXT PRIMARY KEY
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS properties (
                product_name TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (product_name, key),
                FOREIGN KEY (product_name) REFERENCES products(name) ON DELETE CASCADE
            )
        ''')

@app.on_event("startup")
def on_startup():
    init_db()

class ProductAddModel(BaseModel):
    name: str
    model_config = ConfigDict(extra_fields='allow')

class ProductUploadModel(BaseModel):
    name: str
    data: str

def get_properties(product_name, cursor):
    cursor.execute("SELECT key, value FROM properties WHERE product_name = ?", (product_name,))
    return {key: value for key, value in cursor.fetchall()}

@app.post("/add", status_code=201)
async def add_product(product: ProductAddModel):
    name = product.name
    data = product.model_dump()
    with sqlite3.connect('db.sqlite3') as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
        if c.fetchone():
            raise HTTPException(status_code=400, detail="Product already exists")
        c.execute("INSERT INTO products (name) VALUES (?)", (name,))
        for key, value in data.items():
            if key != "name":
                if not isinstance(value, str):
                    raise HTTPException(status_code=400, detail=f"Value for {key} is not a string")
                c.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)", (name, key, value))
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str = Query(...)):
    with sqlite3.connect('db.sqlite3') as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
        if not c.fetchone():
            raise HTTPException(status_code=400, detail="Product not found")
        c.execute("SELECT key, value FROM properties WHERE product_name = ?", (name,))
        properties = c.fetchall()
    data_dict = {key: value for key, value in properties}
    data_json = json.dumps(data_dict)
    return {"data": data_json}

@app.post("/upload")
async def upload_product(product: ProductUploadModel):
    name = product.name
    data_str = product.data
    try:
        data_dict = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    with sqlite3.connect('db.sqlite3') as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
        if not c.fetchone():
            raise HTTPException(status_code=400, detail="Product not found")
        c.execute("DELETE FROM properties WHERE product_name = ?", (name,))
        for key, value in data_dict.items():
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"Value for {key} is not a string")
            c.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)", (name, key, value))
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str = Query(...)):
    with sqlite3.connect('db.sqlite3') as conn:
        c = conn.cursor()
        c.execute("SELECT name FROM products")
        product_names = [row[0] for row in c.fetchall()]
        results = []
        for product_name in product_names:
            properties = get_properties(product_name, c)
            if (query.lower() in product_name.lower()) or any(query.lower() in value.lower() for value in properties.values()):
                product_entry = {"name": product_name}
                product_entry.update(properties)
                results.append(product_entry)
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)