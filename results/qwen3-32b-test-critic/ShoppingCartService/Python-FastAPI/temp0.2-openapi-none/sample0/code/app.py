import sqlite3
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

app = FastAPI()

# Pydantic models for request validation
class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

class RetrieveCartRequest(BaseModel):
    cart_id: str

# Create tables on startup
@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS carts (
            cart_id TEXT PRIMARY KEY
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT,
            item_id INTEGER,
            count INTEGER,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

@app.post("/create_cart", status_code=201)
def create_cart():
    cart_id = str(uuid.uuid4())
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    conn.commit()
    conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
def add_to_cart(request: AddToCartRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Check if item exists in cart_items
    cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", 
                  (request.cart_id, request.item_id))
    existing = cursor.fetchone()

    if existing:
        current_count = existing[0]
        new_count = current_count + request.count
        if new_count < 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        if new_count == 0:
            cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                          (request.cart_id, request.item_id))
        else:
            cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                          (new_count, request.cart_id, request.item_id))
    else:
        if request.count <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                      (request.cart_id, request.item_id, request.count))

    conn.commit()
    conn.close()
    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart")
def retrieve_cart(request: RetrieveCartRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Retrieve items with count > 0
    cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ? AND count > 0", 
                  (request.cart_id,))
    items = cursor.fetchall()
    conn.close()

    # Format the response
    return {"items": [{"item_id": item[0], "count": item[1]} for item in items]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)