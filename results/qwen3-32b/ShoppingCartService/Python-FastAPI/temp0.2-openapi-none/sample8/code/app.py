import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

# Pydantic models
class CartResponse(BaseModel):
    cart_id: str

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

class RetrieveCartRequest(BaseModel):
    cart_id: str

class ItemInCart(BaseModel):
    item_id: int
    count: int

class CartContents(BaseModel):
    items: List[ItemInCart]

# Create database tables on startup
@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS carts (cart_id TEXT PRIMARY KEY)")
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

# Create a new shopping cart
@app.post("/create_cart", response_model=CartResponse, status_code=201)
def create_cart():
    cart_id = str(uuid.uuid4())
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return create_cart()
    finally:
        conn.close()
    return {"cart_id": cart_id}

# Add or remove items from the cart
@app.post("/add_to_cart")
def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Check if item exists in cart
    cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
    existing = cursor.fetchone()

    if existing:
        existing_count = existing[0]
        new_count = existing_count + count
        if new_count < 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        if new_count == 0:
            cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        else:
            cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", 
                          (new_count, cart_id, item_id))
    else:
        if count <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", 
                      (cart_id, item_id, count))

    conn.commit()
    conn.close()
    return {"message": "Item added or removed successfully"}

# Retrieve all items in the cart
@app.post("/retrieve_cart", response_model=CartContents)
def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id

    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Retrieve items
    cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
    items = cursor.fetchall()
    conn.close()

    return {"items": [{"item_id": item[0], "count": item[1]} for item in items]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)