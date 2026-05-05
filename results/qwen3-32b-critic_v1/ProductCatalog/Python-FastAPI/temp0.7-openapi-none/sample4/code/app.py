import sqlite3
import json
from fastapi import FastAPI, HTTPException

app = FastAPI()

def get_db():
    return sqlite3.connect('db.sqlite3')

@app.on_event("startup")
def create_tables():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS properties (
            product_name TEXT,
            key TEXT,
            value TEXT,
            PRIMARY KEY (product_name, key),
            FOREIGN KEY (product_name) REFERENCES products(name) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/add", status_code=201)
async def add_product(product: dict):
    name = product.get('name')
    if not name:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Product already exists")
    
    additional_props = {k: v for k, v in product.items() if k != 'name'}
    
    # Validate input size and content
    if len(additional_props) > 100:
        conn.close()
        raise HTTPException(status_code=400, detail="Too many properties (max 100 allowed)")
    
    for key, value in additional_props.items():
        if not isinstance(value, str):
            conn.close()
            raise HTTPException(status_code=400, detail=f"Property {key} must be a string")
        if len(value) > 1000:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Property {key} exceeds 1000 character limit")
    
    try:
        c.execute("INSERT INTO products (name) VALUES (?)", (name,))
        for key, value in additional_props.items():
            c.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)",
                      (name, key, value))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    
    return {"status": "Product added"}

@app.get("/download")
async def download_product(name: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Product not found")
    
    c.execute("SELECT key, value FROM properties WHERE product_name = ?", (name,))
    props = c.fetchall()
    data = {k: v for k, v in props}
    conn.close()
    
    return {"data": json.dumps(data)}

@app.post("/upload")
async def upload_product(data_entry: dict):
    name = data_entry.get('name')
    data_str = data_entry.get('data')
    
    if not name or not data_str:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Product not found")
    
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid data format")
    
    if not isinstance(data, dict):
        conn.close()
        raise HTTPException(status_code=400, detail="Data must be an object")
    
    if len(data) > 100:
        conn.close()
        raise HTTPException(status_code=400, detail="Too many properties (max 100 allowed)")
    
    for key, value in data.items():
        if not isinstance(value, str):
            conn.close()
            raise HTTPException(status_code=400, detail=f"Property {key} must be a string")
        if len(value) > 1000:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Property {key} exceeds 1000 character limit")
    
    try:
        c.execute("DELETE FROM properties WHERE product_name = ?", (name,))
        for key, value in data.items():
            c.execute("INSERT INTO properties (product_name, key, value) VALUES (?, ?, ?)",
                      (name, key, value))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid upload query")
    finally:
        conn.close()
    
    return {"status": "Product updated"}

@app.get("/search")
async def search_products(query: str):
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('''
            SELECT DISTINCT p.name
            FROM products p
            LEFT JOIN properties pr ON p.name = pr.product_name
            WHERE p.name LIKE ? OR pr.value LIKE ?
        ''', (f'%{query}%', f'%{query}%'))
        
        product_names = [row[0] for row in c.fetchall()][:100]  # Limit results to 100
        
        results = []
        for product_name in product_names:
            c.execute("SELECT key, value FROM properties WHERE product_name = ?", (product_name,))
            props = c.fetchall()
            product_data = {'name': product_name}
            for k, v in props:
                product_data[k] = v
            results.append(product_data)
    finally:
        conn.close()
    
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)