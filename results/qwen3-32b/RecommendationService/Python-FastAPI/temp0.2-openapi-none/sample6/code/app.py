from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel
import sqlite3
import json
from fastapi.responses import HTMLResponse

app = FastAPI()

class ProductCreate(BaseModel):
    product_name: str
    tags: list[str]

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            tags TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/recommender")
async def post_product(product: ProductCreate):
    tags_json = json.dumps(product.tags)
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)", 
                   (product.product_name, tags_json))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/recommender")
async def get_products(tags: str = Query(...)):
    tag_list = [t.strip() for t in tags.split(',')]
    if not tag_list:
        raise HTTPException(status_code=400, detail="No tags provided")
    
    placeholders = ','.join('?' * len(tag_list))
    query = f"""
        SELECT product_name, tags 
        FROM products
        WHERE EXISTS (
            SELECT 1 
            FROM json_each(tags)
            WHERE value IN ({placeholders})
        )
    """
    
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(query, tag_list)
    rows = cursor.fetchall()
    conn.close()
    
    html_content = "<html><body><h1>Recommended Products</h1><ul>"
    for row in rows:
        product_name = row['product_name']
        tags_json = row['tags']
        tags = json.loads(tags_json)
        html_content += f"<li>{product_name} - Tags: {', '.join(tags)}</li>"
    html_content += "</ul></body></html>"
    
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)