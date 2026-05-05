import sqlite3
import json
from fastapi import FastAPI, HTTPException, Request
import uvicorn

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

@app.post("/add", status_code=201)
async def add_product(request: Request):
    body = await request.body()
    if len(body) > 1048576:  # 1MB limit
        raise HTTPException(status_code=413, detail="Request body too large")
    try:
        product = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if "name" not in product:
        raise HTTPException(status_code=400, detail="Missing 'name' field")
    name = product.pop("name")
    if len(product) > 1000:
        raise HTTPException(status_code=400, detail="Too many fields in product")
    for key, value in product.items():
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Field {key} must be a string")
    data = json.dumps(product)
    conn = sqlite3.connect("db.sqlite3")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Product name already exists")
    conn.close()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str):
    conn = sqlite3.connect("db.sqlite3")
    c = conn.cursor()
    c.execute("SELECT data FROM products WHERE name = ?", (name,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=400, detail="Product not found")
    data_str = result[0]
    product_data = {"name": name}
    if data_str:
        try:
            product_data.update(json.loads(data_str))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid data format")
    return {"data": json.dumps(product_data)}

@app.post("/upload")
async def upload_product(request: Request):
    body = await request.body()
    if len(body) > 1048576:  # 1MB limit
        raise HTTPException(status_code=413, detail="Request body too large")
    try:
        product = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    name = product.get("name")
    data_str = product.get("data")
    if not name or not data_str:
        raise HTTPException(status_code=400, detail="Missing 'name' or 'data'")
    try:
        data_dict = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    if data_dict.get("name") != name:
        raise HTTPException(status_code=400, detail="Name in data does not match request name")
    if len(data_dict) > 1000:
        raise HTTPException(status_code=400, detail="Too many fields in data")
    for key, value in data_dict.items():
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Field {key} must be a string")
    data_to_store = json.dumps(data_dict)
    conn = sqlite3.connect("db.sqlite3")
    c = conn.cursor()
    try:
        c.execute("REPLACE INTO products (name, data) VALUES (?, ?)", (name, data_to_store))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid upload query")
    conn.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    if len(query) > 100:
        raise HTTPException(status_code=400, detail="Query too long")
    conn = sqlite3.connect("db.sqlite3")
    c = conn.cursor()
    c.execute("""
        SELECT name, data FROM products 
        WHERE name LIKE ? OR data LIKE ?
        LIMIT 100
    """, (f"%{query}%", f"%{query}%"))
    results = c.fetchall()
    conn.close()
    products = []
    for name, data_str in results:
        product_data = {"name": name}
        if data_str:
            try:
                product_data.update(json.loads(data_str))
            except json.JSONDecodeError:
                continue
        products.append(product_data)
    return {"results": products}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)