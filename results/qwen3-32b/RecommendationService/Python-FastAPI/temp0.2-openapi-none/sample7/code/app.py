import sqlite3
import json
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

class ProductModel(BaseModel):
    product_name: str
    tags: list[str]

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            tags TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/recommender")
async def post_product(product: ProductModel):
    try:
        tags_json = json.dumps(product.tags)
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)", 
                  (product.product_name, tags_json))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid input")

@app.get("/recommender", response_class=HTMLResponse)
async def get_products(tags: str = Query(...)):
    try:
        tag_list = [tag.strip() for tag in tags.split(',')]
        if not tag_list:
            return "<html><body>No matching products</body></html>"
        placeholders = ','.join('?' * len(tag_list))
        query = f"""
        SELECT product_name FROM products
        WHERE EXISTS (
            SELECT 1
            FROM json_each(products.tags)
            WHERE value IN ({placeholders})
        )
        """
        conn = sqlite3.connect('db.sqlite3')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(query, tag_list)
        results = c.fetchall()
        conn.close()
        html_content = "<html><body><h1>Recommended Products</h1><ul>"
        for row in results:
            product_name = row['product_name']
            html_content += f"<li><a href='#'>{product_name}</a></li>"
        html_content += "</ul></body></html>"
        return html_content
    except Exception as e:
        return HTMLResponse(content=f"<html><body>Error processing request</body></html>", status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)