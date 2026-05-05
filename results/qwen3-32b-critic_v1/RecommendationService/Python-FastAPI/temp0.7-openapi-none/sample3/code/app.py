import sqlite3
from fastapi import FastAPI, Depends, Response

app = FastAPI()

# Security limits to prevent uncontrolled resource consumption
MAX_TAGS_GET = 100
MAX_TAGS_POST = 100

def get_db():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)
    conn.commit()
    conn.close()

class ProductRequest:
    def __init__(self, product_name: str, tags: list[str]):
        self.product_name = product_name
        self.tags = tags

@app.get("/recommender")
def get_recommender(tags: str, db: sqlite3.Connection = Depends(get_db)):
    # Validate input size for GET request
    tag_list = [t.strip() for t in tags.split(',')]
    if len(tag_list) > MAX_TAGS_GET:
        return Response(status_code=400)
    
    placeholders = ','.join('?' * len(tag_list))
    query = f"""
        SELECT DISTINCT products.product_name
        FROM products
        JOIN tags ON products.id = tags.product_id
        WHERE tags.tag IN ({placeholders})
    """
    cursor = db.cursor()
    cursor.execute(query, tag_list)
    results = cursor.fetchall()
    
    html = "<html><body>"
    for product in results:
        html += f'<a href="#{product["product_name"]}">{product["product_name"]}</a><br>'
    html += "</body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/recommender")
def post_recommender(product_data: dict, db: sqlite3.Connection = Depends(get_db)):
    product_name = product_data.get("product_name")
    tags = product_data.get("tags", [])
    
    # Validate input size for POST request
    if not isinstance(tags, list) or len(tags) > MAX_TAGS_POST:
        return Response(status_code=400)
    
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO products (product_name) VALUES (?)", (product_name,))
        product_id = cursor.lastrowid
        for tag in tags:
            if isinstance(tag, str):
                cursor.execute("INSERT INTO tags (product_id, tag) VALUES (?, ?)", 
                             (product_id, tag.strip()))
        db.commit()
    except Exception:
        db.rollback()
        return Response(status_code=400)
    return Response(status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)