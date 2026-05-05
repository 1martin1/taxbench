import sqlite3
from fastapi import FastAPI, HTTPException, Body, Query
import json

app = FastAPI()
MAX_DATA_SIZE = 1024 * 1024  # 1MB

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

@app.post("/add", status_code=201)
async def add_product(product_data: dict = Body(...)):
    name = product_data.get("name")
    if not name or "name" not in product_data:
        raise HTTPException(status_code=400, detail="Name is required")
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE name = ?", (name,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Product already exists")
    
    data = product_data.copy()
    data.pop("name")
    data_json = json.dumps(data)
    if len(data_json) > MAX_DATA_SIZE:
        conn.close()
        raise HTTPException(status_code=400, detail="Data too large")
    
    cursor.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data_json))
    conn.commit()
    conn.close()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str = Query(...)):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT name, data FROM products WHERE name = ?", (name,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=400, detail="Product not found")
    
    data = json.loads(row[1]) if row[1] else {}
    full_data = {"name": row[0], **data}
    return {"data": full_data}

@app.post("/upload")
async def upload_product(name: str = Body(...), data: str = Body(...)):
    if len(data) > MAX_DATA_SIZE:
        raise HTTPException(status_code=400, detail="Data too large")
    try:
        data_dict = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    
    if "name" not in data_dict or data_dict["name"] != name:
        raise HTTPException(status_code=400, detail="Name mismatch in data")
    
    data_to_store = data_dict.copy()
    data_to_store.pop("name")
    data_json = json.dumps(data_to_store)
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO products (name, data)
        VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET data = excluded.data
    """, (name, data_json))
    conn.commit()
    conn.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str = Query(...)):
    if len(query) > 100:
        raise HTTPException(status_code=400, detail="Query too long")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name, data FROM products
        WHERE name LIKE ? OR data LIKE ?
        LIMIT 100
    """, (f"%{query}%", f"%{query}%",))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        name, data_json = row
        data = json.loads(data_json) if data_json else {}
        full_data = {"name": name, **data}
        results.append(full_data)
    
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)