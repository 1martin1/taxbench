import os
import uuid
import sqlite3
from typing import List, Dict

from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel, Field

# -------------------------------------------------
# Database utilities
# -------------------------------------------------
DB_PATH = "db.sqlite3"

def get_db():
    # Simple dependency that yields a connection
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # carts table just stores the cart identifier
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS carts (
            cart_id TEXT PRIMARY KEY
        )
        """
    )
    # cart_items stores each item per cart
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT,
            item_id INTEGER,
            count INTEGER,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()

# -------------------------------------------------
# Pydantic models
# -------------------------------------------------
class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")

class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")

class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")

class CartItem(BaseModel):
    item_id: int
    count: int

class RetrieveCartResponse(BaseModel):
    items: List[CartItem]

# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

@app.on_event("startup")
def on_startup():
    init_db()
    # Load APP_SECRET if needed (not used in this simple example)
    _ = os.getenv("APP_SECRET")

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
@app.post("/create_cart", response_model=CreateCartResponse, status_code=status.HTTP_201_CREATED)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    cart_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    return CreateCartResponse(cart_id=cart_id)

@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
def add_to_cart(payload: AddToCartRequest, db: sqlite3.Connection = Depends(get_db)):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Check if item already exists in cart
    cur = db.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (payload.cart_id, payload.item_id)
    )
    row = cur.fetchone()
    if row:
        new_count = row["count"] + payload.count
        if new_count > 0:
            db.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id)
            )
        else:
            # Remove the item if count drops to zero or below
            db.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id)
            )
    else:
        if payload.count <= 0:
            raise HTTPException(status_code=400, detail="Cannot add non‑positive count for a new item")
        db.execute(
            "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
            (payload.cart_id, payload.item_id, payload.count)
        )
    db.commit()
    return {"detail": "Item added or removed successfully"}

@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
def retrieve_cart(payload: RetrieveCartRequest, db: sqlite3.Connection = Depends(get_db)):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    cur = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (payload.cart_id,)
    )
    items = [CartItem(item_id=row["item_id"], count=row["count"]) for row in cur.fetchall()]
    return RetrieveCartResponse(items=items)

# -------------------------------------------------
# Run the server (if executed directly)
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)