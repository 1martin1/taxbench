import sqlite3
import json
import html
from fastapi import FastAPI, HTTPException, Query, Response

app = FastAPI()

def get_db():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def create_table():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            tags TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/recommender")
async def post_product(product: dict):
    product_name = product.get("product_name")
    tags = product.get("tags")
    if not product_name or not tags or not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="Invalid input")
    tags_json = json.dumps(tags)
    conn = get_db()
    conn.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)", (product_name, tags_json))
    conn.commit()
    conn.close()
    return Response(status_code=200)

@app.get("/recommender", responses={422: {"description": "Validation error"}})
async def get_products(tags: str = Query(..., max_length=1024)):
    tag_list = [t.strip() for t in tags.split(',')]
    if len(tag_list) > 100:
        raise HTTPException(status_code=422, detail="Too many tags")
    conditions = " OR ".join("JSON_CONTAINS(tags, ?)" for _ in tag_list)
    query = f"SELECT product_name FROM products WHERE {conditions}"
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(query, tag_list)
    products = cursor.fetchall()
    conn.close()
    html = "<html><body><h1>Products</h1><ul>"
    for product in products:
        html += f"<li>{html.escape(product['product_name'])}</li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)