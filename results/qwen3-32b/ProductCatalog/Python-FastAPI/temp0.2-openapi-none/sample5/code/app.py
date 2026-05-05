from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
import sqlite3
import json
import os

app = FastAPI()

class UploadData(BaseModel):
    name: str
    data: str

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products
                 (name TEXT PRIMARY KEY, properties TEXT)''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/add")
async def add_product(product: dict):
    name = product.get('name')
    if not name:
        raise HTTPException(status_code=400, detail="Missing name")
    properties = {k: v for k, v in product.items() if k != 'name'}
    
    # Validate all additional properties are strings
    for key, value in properties.items():
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Property {key} must be a string")
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (name, properties) VALUES (?, ?)",
                  (name, json.dumps(properties)))
        conn.commit()
        return Response(status_code=201)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product already exists")
    finally:
        conn.close()

@app.get("/download")
async def download_product(name: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT name, properties FROM products WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=400, detail="Product not found")
    product_name, properties_json = row
    properties = json.loads(properties_json)
    data = {
        "name": product_name,
        **properties
    }
    data_str = json.dumps(data)
    return {"data": data_str}

@app.post("/upload")
async def upload_product(upload_data: UploadData):
    name = upload_data.name
    data = upload_data.data
    try:
        product_data = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    
    if 'name' not in product_data or product_data['name'] != name:
        raise HTTPException(status_code=400, detail="Data name mismatch")
    
    properties = {k: v for k, v in product_data.items() if k != 'name'}
    
    # Validate all properties are strings
    for key, value in properties.items():
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Property {key} must be a string")
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("REPLACE INTO products (name, properties) VALUES (?, ?)",
                  (name, json.dumps(properties)))
        conn.commit()
        return {"message": "Product updated"}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid data")
    finally:
        conn.close()

@app.get("/search")
async def search_products(query: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT name, properties FROM products")
    rows = c.fetchall()
    conn.close()
    
    query_lower = query.lower()
    results = []
    for product_name, properties_json in rows:
        properties = json.loads(properties_json)
        # Check product name
        if query_lower in product_name.lower():
            results.append({
                "name": product_name,
                **properties
            })
            continue
        # Check properties
        for value in properties.values():
            if isinstance(value, str) and query_lower in value.lower():
                results.append({
                    "name": product_name,
                    **properties
                })
                break
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)