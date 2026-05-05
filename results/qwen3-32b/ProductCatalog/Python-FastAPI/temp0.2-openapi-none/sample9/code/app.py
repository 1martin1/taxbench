from fastapi import FastAPI, HTTPException
import sqlite3
import json
from pydantic import BaseModel

app = FastAPI()

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

class UploadItem(BaseModel):
    name: str
    data: str

@app.on_event("startup")
def create_tables():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()

@app.post("/add", status_code=201)
async def add_product(product: dict):
    if 'name' not in product:
        raise HTTPException(status_code=400, detail="Missing name")
    name = product['name']
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT name FROM products WHERE name = ?", (name,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="Product already exists")
    data_json = json.dumps(product)
    cursor.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data_json))
    db.commit()
    db.close()
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cursor.fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=400, detail="Product not found")
    return {"data": row['data']}

@app.post("/upload")
async def upload_product(item: UploadItem):
    name = item.name
    data_str = item.data
    try:
        data_dict = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    if 'name' not in data_dict:
        raise HTTPException(status_code=400, detail="Data missing name")
    if data_dict['name'] != name:
        raise HTTPException(status_code=400, detail="Name mismatch")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE products SET data = ? WHERE name = ?", (data_str, name))
    if cursor.rowcount == 0:
        db.close()
        raise HTTPException(status_code=400, detail="Product not found")
    db.commit()
    db.close()
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT name, data FROM products")
    all_products = cursor.fetchall()
    db.close()
    results = []
    for row in all_products:
        product_data = json.loads(row['data'])
        if any(query.lower() in str(value).lower() for value in product_data.values()):
            results.append(product_data)
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)