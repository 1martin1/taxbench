from fastapi import FastAPI, HTTPException
import sqlite3
import json
from typing import Dict, Any, List

app = FastAPI()

def get_db():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def create_tables():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/add")
async def add_product(product_data: Dict[str, Any]):
    if 'name' not in product_data:
        raise HTTPException(status_code=400, detail="Missing 'name' field")
    name = product_data['name']
    if len(name) > 1000:
        raise HTTPException(status_code=400, detail="Name too long")
    if len(product_data) - 1 > 100:  # Exclude 'name' field
        raise HTTPException(status_code=400, detail="Too many additional properties")
    for key, value in product_data.items():
        if key != 'name':
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"Field '{key}' must be a string")
            if len(value) > 1000:
                raise HTTPException(status_code=400, detail=f"Field '{key}' too long")
    data_str = json.dumps(product_data)
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO products (name, data) VALUES (?, ?)", (name, data_str))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"data": ""}
    data_str = row['data']
    return {"data": data_str}

@app.post("/upload")
async def upload_product(product: Dict[str, Any]):
    if 'name' not in product or 'data' not in product:
        raise HTTPException(status_code=400, detail="Missing 'name' or 'data' field")
    name = product['name']
    data_str = product['data']
    if len(data_str) > 100000:
        raise HTTPException(status_code=400, detail="Data too large")
    try:
        data_dict = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'data'")
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO products (name, data) VALUES (?, ?)", (name, data_str))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    finally:
        conn.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    if len(query) < 1 or len(query) > 100:
        raise HTTPException(status_code=400, detail="Query length invalid")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, data FROM products")
    results = []
    while (row := cursor.fetchone()) is not None:
        name = row['name']
        data_str = row['data']
        try:
            data_dict = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if (query in name) or any(query in key for key in data_dict) or any(query in value for value in data_dict.values()):
            result = {'name': name}
            for key, value in data_dict.items():
                if key != 'name':
                    result[key] = value
            results.append(result)
            if len(results) >= 100:  # Limit results to prevent excessive processing
                break
    conn.close()
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)