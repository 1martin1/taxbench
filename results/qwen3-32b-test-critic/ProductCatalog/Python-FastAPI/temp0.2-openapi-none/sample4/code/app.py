from fastapi import FastAPI, HTTPException
import sqlite3
import json
from pydantic import BaseModel, ConfigDict

app = FastAPI()

# Database setup
conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
cursor = conn.cursor()

# Create tables if not exist
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
        PRIMARY KEY (product_name, key),
        FOREIGN KEY (product_name) REFERENCES products(name) ON DELETE CASCADE
    )
''')
conn.commit()

# Pydantic models
class ProductAdd(BaseModel):
    name: str
    model_config = ConfigDict(extra='allow')

class UploadData(BaseModel):
    name: str
    data: str

@app.post("/add", status_code=201)
async def add_product(product: ProductAdd):
    name = product.name
    cursor.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Product already exists")
    cursor.execute("INSERT INTO products (name) VALUES (?)", (name,))
    for key, value in product.model_extra.items():
        cursor.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)", (name, key, value))
    conn.commit()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str):
    cursor.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if not cursor.fetchone():
        raise HTTPException(status_code=400, detail="Product not found")
    cursor.execute("SELECT key, value FROM properties WHERE product_name = ?", (name,))
    props = {k: v for k, v in cursor.fetchall() if k != 'name'}
    data_str = json.dumps(props)
    return {"data": data_str}

@app.post("/upload")
async def upload_product(data_entry: UploadData):
    name = data_entry.name
    data_str = data_entry.data
    cursor.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    exists = cursor.fetchone()
    if not exists:
        cursor.execute("INSERT INTO products (name) VALUES (?)", (name,))
    cursor.execute("DELETE FROM properties WHERE product_name = ?", (name,))
    try:
        props = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    for key, value in props.items():
        if key != 'name':
            cursor.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)", (name, key, value))
    conn.commit()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    cursor.execute("SELECT name FROM products WHERE name LIKE ?", (f"%{query}%",))
    name_matches = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT product_name FROM properties WHERE value LIKE ?", (f"%{query}%",))
    prop_matches = {row[0] for row in cursor.fetchall()}
    all_matches = name_matches.union(prop_matches)
    results = []
    for product_name in all_matches:
        cursor.execute("SELECT key, value FROM properties WHERE product_name = ?", (product_name,))
        props = {k: v for k, v in cursor.fetchall() if k != 'name'}
        results.append({"name": product_name, **props})
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)