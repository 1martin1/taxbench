from fastapi import FastAPI, HTTPException
import sqlite3
from pydantic import BaseModel
from pydantic.config import ConfigDict
from typing import Dict, Any, List, Optional
import json

app = FastAPI()

def get_db():
    return sqlite3.connect('db.sqlite3')

@app.on_event("startup")
def create_tables():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS properties (
            product_name TEXT,
            key TEXT,
            value TEXT,
            FOREIGN KEY(product_name) REFERENCES products(name)
        )
    ''')
    conn.commit()
    conn.close()

class AddProductModel(BaseModel):
    name: str
    model_config = ConfigDict(extra = 'allow')

@app.post("/add")
async def add_product(product: AddProductModel):
    conn = get_db()
    cursor = conn.cursor()
    
    # Validate all extra fields are strings
    for key, value in product.model_extra.items():
        if not isinstance(value, str):
            conn.close()
            raise HTTPException(status_code=400, detail=f"Property {key} must be a string")
    
    # Check if product exists
    cursor.execute("SELECT 1 FROM products WHERE name = ?", (product.name,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Product already exists")
    
    # Insert into products
    cursor.execute("INSERT INTO products (name) VALUES (?)", (product.name,))
    
    # Insert properties
    for key, value in product.model_extra.items():
        cursor.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)",
                       (product.name, key, value))
    
    conn.commit()
    conn.close()
    return {"status": "Product added"}, 201

@app.get("/download")
async def download_product(name: str):
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if product exists
    cursor.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Product not found")
    
    # Get all properties
    cursor.execute("SELECT key, value FROM properties WHERE product_name = ?", (name,))
    properties = dict(cursor.fetchall())
    conn.close()
    
    return {"data": json.dumps(properties)}

class UploadProductModel(BaseModel):
    name: str
    data: str

@app.post("/upload")
async def upload_product(product: UploadProductModel):
    name = product.name
    data_str = product.data
    
    try:
        properties = json.loads(data_str)
        # Validate all values are strings
        for key, value in properties.items():
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"Property {key} must be a string")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if product exists, if not, insert
    cursor.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO products (name) VALUES (?)", (name,))
    
    # Delete existing properties
    cursor.execute("DELETE FROM properties WHERE product_name = ?", (name,))
    
    # Insert new properties
    for key, value in properties.items():
        cursor.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)",
                       (name, key, value))
    
    conn.commit()
    conn.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    search_term = f"%{query}%"
    conn = get_db()
    cursor = conn.cursor()
    
    # Get product names that match name or any property
    cursor.execute("""
        SELECT name FROM products WHERE name LIKE ?
        UNION
        SELECT DISTINCT product_name FROM properties 
        WHERE key LIKE ? OR value LIKE ?
    """, (search_term, search_term, search_term))
    results = cursor.fetchall()
    product_names = [row[0] for row in results]
    
    # For each product name, get all properties
    products = []
    for name in product_names:
        cursor.execute("SELECT key, value FROM properties WHERE product_name = ?", (name,))
        props = dict(cursor.fetchall())
        product = {"name": name, **props}
        products.append(product)
    
    conn.close()
    return {"results": products}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)