import sqlite3
import json
import html
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Initialize database
def init_db():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

@app.post("/add")
async def add_product(product: dict):
    if 'name' not in product:
        raise HTTPException(status_code=400, detail="Invalid input")
    name = product['name']
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM products WHERE name = ?", (name,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input")
    try:
        data_json = json.dumps(product)
    except:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input")
    cursor.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data_json))
    conn.commit()
    conn.close()
    return Response(status_code=201)

@app.get("/download")
async def download_product(name: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid input")
    return {"data": row[0]}

@app.post("/upload")
async def upload_product(product_data: dict):
    name = product_data.get('name')
    data = product_data.get('data')
    if not name or not data:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    try:
        json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO products (name, data) VALUES (?, ?)", (name, data))
    conn.commit()
    conn.close()
    return Response(status_code=200)

@app.get("/search")
async def search_products(query: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM products")
    all_data = cursor.fetchall()
    conn.close()
    results = []
    for row in all_data:
        data_str = row[0]
        try:
            product = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        found = False
        for key, value in product.items():
            if query.lower() in key.lower() or query.lower() in value.lower():
                found = True
                break
        if found:
            escaped_product = {}
            for k, v in product.items():
                if isinstance(v, str):
                    escaped_product[k] = html.escape(v)
                else:
                    escaped_product[k] = v
            results.append(escaped_product)
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)