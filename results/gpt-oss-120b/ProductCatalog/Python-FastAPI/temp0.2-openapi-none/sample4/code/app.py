import os
import json
import sqlite3
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Product Catalog", version="1.0.11")


class ProductBase(BaseModel):
    name: str

    class Config:
        extra = "allow"  # allow any additional string fields


class UploadModel(BaseModel):
    name: str
    data: str


@app.on_event("startup")
def startup():
    db_path = "db.sqlite3"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
        """
    )
    conn.commit()
    app.state.db = conn


@app.on_event("shutdown")
def shutdown():
    db = app.state.db
    db.close()


@app.post("/add", status_code=201)
def add_product(product: ProductBase):
    """
    Add a new product. Any extra fields are stored as‑is.
    """
    db = app.state.db
    product_dict = product.model_dump()
    json_data = json.dumps(product_dict)

    try:
        db.execute(
            "INSERT INTO products (name, data) VALUES (?, ?)",
            (product.name, json_data),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Product with this name already exists")

    return {"message": "Product added"}


@app.get("/download")
def download(name: str = Query(..., description="The name of the product")):
    """
    Return the stored JSON string for the given product name.
    """
    db = app.state.db
    cur = db.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"data": row[0]}


@app.post("/upload")
def upload(upload: UploadModel):
    """
    Replace (or create) a product entry with the supplied JSON data.
    The `data` field must be a JSON string that contains a matching `name`.
    """
    # Validate that `data` is proper JSON
    try:
        data_dict = json.loads(upload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in `data` field")

    # Ensure the name inside the JSON matches the provided name
    if data_dict.get("name") != upload.name:
        raise HTTPException(status_code=400, detail="Name mismatch between payload and JSON data")

    db = app.state.db
    db.execute(
        "INSERT OR REPLACE INTO products (name, data) VALUES (?, ?)",
        (upload.name, upload.data),
    )
    db.commit()
    return {"message": "Product updated"}


@app.get("/search")
def search(query: str = Query(..., description="Search term for product name or any property")):
    """
    Search for products whose name or any string property contains the query (case‑insensitive).
    """
    db = app.state.db
    cur = db.execute("SELECT data FROM products")
    rows = cur.fetchall()

    q_lower = query.lower()
    results = []

    for (data_str,) in rows:
        try:
            prod = json.loads(data_str)
        except json.JSONDecodeError:
            continue  # skip malformed rows

        # Check if any value (including name) contains the query substring
        if any(q_lower in str(value).lower() for value in prod.values()):
            results.append(prod)

    return {"results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)