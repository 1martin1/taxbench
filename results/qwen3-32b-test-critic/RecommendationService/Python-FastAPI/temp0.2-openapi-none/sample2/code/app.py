from fastapi import FastAPI, Response, HTTPException, Request
from pydantic import BaseModel, field_validator
from typing import List
import sqlite3
from contextlib import asynccontextmanager
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI()

@asynccontextmanager
async def lifespan(app: FastAPI):
    def init_db():
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS products
                     (id INTEGER PRIMARY KEY, product_name TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS product_tags
                     (product_id INTEGER, tag TEXT, FOREIGN KEY(product_id) REFERENCES products(id))''')
        conn.commit()
        conn.close()
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

class ProductCreate(BaseModel):
    product_name: str
    tags: List[str]

    @field_validator('product_name')
    def product_name_not_empty(cls, v):
        if not v.strip():
            raise ValueError('product_name cannot be empty')
        return v

    @field_validator('tags')
    def tags_not_empty(cls, v):
        for tag in v:
            if not tag.strip():
                raise ValueError('tags cannot be empty')
        return v

@app.post("/recommender")
async def post_product(product: ProductCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (product_name) VALUES (?)", (product.product_name,))
        product_id = c.lastrowid
        for tag in product.tags:
            c.execute("INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", (product_id, tag))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

@app.get("/recommender")
async def get_products(tags: str):
    tag_list = [tag.strip() for tag in tags.split(',')]
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    query = """
    SELECT DISTINCT products.product_name 
    FROM products 
    JOIN product_tags ON products.id = product_tags.product_id 
    WHERE product_tags.tag IN ({})
    """.format(','.join('?'*len(tag_list)))
    c.execute(query, tag_list)
    results = c.fetchall()
    conn.close()
    products = [row[0] for row in results]
    html_content = "<html><body><h1>Recommended Products</h1><ul>"
    for product in products:
        html_content += f"<li><a href='#'>{product}</a></li>"
    html_content += "</ul></body></html>"
    return Response(content=html_content, media_type="text/html")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid input"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)