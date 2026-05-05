import sqlite3
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic.config import ConfigDict
from typing import Optional, Dict, Any

app = FastAPI()

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

class AddProductModel(BaseModel):
    name: str
    model_config = ConfigDict(extra='allow')

class UploadProductModel(BaseModel):
    name: str
    data: str

@app.post("/add", status_code=201)
async def add_product(product: AddProductModel):
    name = product.name
    data_dict = product.model_dump()
    del data_dict['name']
    data_json = json.dumps(data_dict)
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute('INSERT INTO products (name, data) VALUES (?, ?)', (name, data_json))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product already exists")
    finally:
        conn.close()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('SELECT data FROM products WHERE name = ?', (name,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=400, detail="Product not found")
    return {"data": row[0]}

@app.post("/upload")
async def upload_product(product: UploadProductModel):
    name = product.name
    data_str = product.data
    try:
        json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute('REPLACE INTO products (name, data) VALUES (?, ?)', (name, data_str))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    finally:
        conn.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('SELECT name, data FROM products')
    all_products = c.fetchall()
    conn.close()
    results = []
    for name, data_str in all_products:
        try:
            data_dict = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if query.lower() in name.lower():
            results.append({**{"name": name}, **data_dict})
            continue
        for value in data_dict.values():
            if query.lower() in value.lower():
                results.append({**{"name": name}, **data_dict})
                break
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)