from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import List
import sqlite3
import uuid

app = FastAPI()

# Pydantic models
class CreateCartResponse(BaseModel):
    cart_id: str

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

class RetrieveCartRequest(BaseModel):
    cart_id: str

class CartItem(BaseModel):
    item_id: int
    count: int

class RetrieveCartResponse(BaseModel):
    items: List[CartItem]

# Database initialization
@app.on_event("startup")
def startup():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS carts (cart_id TEXT PRIMARY KEY)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            cart_id TEXT,
            item_id INTEGER,
            count INTEGER,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id)
        )
    """)
    conn.commit()
    conn.close()

# Endpoints
@app.post("/create_cart", status_code=201, response_model=CreateCartResponse)
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
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Check if item exists in items
    cursor.execute("SELECT count FROM items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
    existing = cursor.fetchone()

    if existing:
        current_count = existing[0]
        new_count = current_count + count
        if new_count < 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        elif new_count == 0:
            cursor.execute("DELETE FROM items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        else:
            cursor.execute("UPDATE items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
    else:
        if count <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        cursor.execute("INSERT INTO items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, count))

    conn.commit()
    conn.close()
    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Get all items in the cart
    cursor.execute("SELECT item_id, count FROM items WHERE cart_id = ?", (cart_id,))
    rows = cursor.fetchall()
    items = [{"item_id": row[0], "count": row[1]} for row in rows]
    conn.close()
    return {"items": items}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)