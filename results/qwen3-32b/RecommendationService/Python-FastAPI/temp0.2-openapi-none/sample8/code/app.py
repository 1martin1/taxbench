import sqlite3
import json
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            product_name TEXT PRIMARY KEY,
            tags TEXT
        )
    """)
    conn.commit()
    conn.close()

class ProductRequest(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        tags_json = json.dumps(product.tags)
        cursor.execute("INSERT INTO products (product_name, tags) VALUES (?, ?)",
                       (product.product_name, tags_json))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product already exists")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

@app.get("/recommender")
async def get_products(tags: str):
    tags_list = [tag.strip() for tag in tags.split(",")]
    if not tags_list:
        raise HTTPException(status_code=400, detail="Tags are required")
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    
    query_parts = ["JSON_CONTAINS(tags, json_quote(?)) = 1"] * len(tags_list)
    query = " OR ".join(query_parts)
    sql = f"SELECT product_name, tags FROM products WHERE {query}"
    
    try:
        cursor.execute(sql, tags_list)
        results = cursor.fetchall()
    except Exception:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    
    html = "<html><body><ul>"
    for product_name, _ in results:
        html += f"<li>{product_name}</li>"
    html += "</ul></body></html>"
    
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)