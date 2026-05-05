import sqlite3
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
import json

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

class AddProductModel(BaseModel):
    name: str
    model_config = ConfigDict(extra = 'allow')

class UploadProductModel(BaseModel):
    name: str
    data: str
    model_config = ConfigDict(extra = 'forbid')

@app.post("/add")
async def add_product(product: AddProductModel):
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO products (name) VALUES (?)", (product.name,))
        product_id = cursor.lastrowid
        for key, value in product.model_extra.items():
            cursor.execute("INSERT INTO properties (product_id, key, value) VALUES (?, ?, ?)",
                           (product_id, key, value))
        conn.commit()
        conn.close()
        return Response(status_code=201)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product name already exists")
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid input")

@app.get("/download")
async def download_product(name: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM products WHERE name = ?", (name,))
    product_row = cursor.fetchone()
    if not product_row:
        conn.close()
        raise HTTPException(status_code=400, detail="Product not found")
    product_id = product_row[0]
    cursor.execute("SELECT key, value FROM properties WHERE product_id = ?", (product_id,))
    properties = cursor.fetchall()
    data = {"name": name}
    for key, value in properties:
        data[key] = value
    conn.close()
    return {"data": json.dumps(data)}

@app.post("/upload")
async def upload_product(upload: UploadProductModel):
    name = upload.name
    data_str = upload.data
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    if "name" not in data:
        raise HTTPException(status_code=400, detail="Data missing 'name' field")
    if data["name"] != name:
        raise HTTPException(status_code=400, detail="Data name does not match product name")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM products WHERE name = ?", (name,))
    product_row = cursor.fetchone()
    if not product_row:
        conn.close()
        raise HTTPException(status_code=400, detail="Product not found")
    product_id = product_row[0]
    cursor.execute("DELETE FROM properties WHERE product_id = ?", (product_id,))
    for key, value in data.items():
        if key == "name":
            continue
        cursor.execute("INSERT INTO properties (product_id, key, value) VALUES (?, ?, ?)",
                       (product_id, key, value))
    conn.commit()
    conn.close()
    return Response(status_code=200)

@app.get("/search")
async def search_products(query: str = Query(..., description="Search query")):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name
        FROM products p
        WHERE p.name LIKE ?
        UNION
        SELECT DISTINCT p2.id, p2.name
        FROM properties prop
        JOIN products p2 ON prop.product_id = p2.id
        WHERE prop.value LIKE ?
    """, (f"%{query}%", f"%{query}%",))
    results = cursor.fetchall()
    conn.close()
    if not results:
        return {"results": []}
    products_data = []
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    for product_id, product_name in results:
        cursor.execute("SELECT key, value FROM properties WHERE product_id = ?", (product_id,))
        properties = cursor.fetchall()
        product_data = {"name": product_name}
        for key, value in properties:
            product_data[key] = value
        products_data.append(product_data)
    conn.close()
    return {"results": products_data}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)