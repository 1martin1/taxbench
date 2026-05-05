from fastapi import FastAPI, HTTPException, Query
import sqlite3
import json
import os

app = FastAPI()

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            data TEXT
        )
    ''')
    conn.commit()
    conn.close()

def product_exists(name: str) -> bool:
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

@app.post("/add", status_code=201)
async def add_product(product: dict):
    name = product.get('name')
    if not name:
        raise HTTPException(status_code=400, detail="Invalid input")
    product_data = {k: v for k, v in product.items() if k != 'name'}
    for k, v in product_data.items():
        if not isinstance(v, str):
            raise HTTPException(status_code=400, detail="All additional properties must be strings")
    data_json = json.dumps(product_data)
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data_json))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product name already exists")
    finally:
        conn.close()
    return {"message": "Product successfully added"}

@app.get("/download")
async def download_product(name: str = Query(...)):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT data FROM products WHERE name = ?", (name,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=400, detail="Invalid input")
    return {"data": result[0]}

@app.post("/upload")
async def upload_product(product: dict):
    name = product.get('name')
    data = product.get('data')
    if not name or not data:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    try:
        json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("UPDATE products SET data = ? WHERE name = ?", (data, name))
    conn.commit()
    conn.close()
    if not product_exists(name):
        raise HTTPException(status_code=400, detail="Invalid upload query")
    return {"message": "Product with the given name was updated"}

@app.get("/search")
async def search_products(query: str = Query(...)):
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    sql = """
        SELECT name, data FROM products
        WHERE name LIKE ?
        OR JSON_SEARCH(data, 'all', ?, '$**') IS NOT NULL
    """
    params = ('%' + query + '%', '%' + query + '%')
    c.execute(sql, params)
    results = c.fetchall()
    conn.close()
    products = []
    for row in results:
        product = {"name": row['name']}
        try:
            data = json.loads(row['data'])
            product.update(data)
        except json.JSONDecodeError:
            pass
        products.append(product)
    return {"results": products}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)