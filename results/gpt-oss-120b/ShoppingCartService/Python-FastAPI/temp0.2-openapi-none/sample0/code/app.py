import os
import uuid
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

# Initialize FastAPI app
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

# SQLite database connection (shared, thread‑safe)
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def init_db() -> None:
    """Create tables if they don't exist."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                id TEXT PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT,
                item_id INTEGER,
                count INTEGER,
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(id) ON DELETE CASCADE
            )
            """
        )


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


@app.on_event("startup")
def on_startup():
    init_db()


@app.post("/create_cart", response_model=CreateCartResponse, status_code=status.HTTP_201_CREATED)
def create_cart():
    """Create a new shopping cart and return its identifier."""
    cart_id = str(uuid.uuid4())
    try:
        with conn:
            conn.execute("INSERT INTO carts (id) VALUES (?)", (cart_id,))
    except sqlite3.IntegrityError:
        # Extremely unlikely due to UUID uniqueness, but handle gracefully
        raise HTTPException(status_code=500, detail="Failed to create cart")
    return CreateCartResponse(cart_id=cart_id)


@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
def add_to_cart(payload: AddToCartRequest):
    """Add or remove items from a cart."""
    # Validate cart existence
    cart = conn.execute("SELECT 1 FROM carts WHERE id = ?", (payload.cart_id,)).fetchone()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    if payload.count == 0:
        raise HTTPException(status_code=400, detail="Count cannot be zero")

    with conn:
        existing = conn.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        ).fetchone()

        if existing:
            new_count = existing["count"] + payload.count
            if new_count > 0:
                conn.execute(
                    "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                    (new_count, payload.cart_id, payload.item_id),
                )
            else:
                # Remove the item if count drops to zero or below
                conn.execute(
                    "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                    (payload.cart_id, payload.item_id),
                )
        else:
            # No existing entry; only allow positive count to insert
            if payload.count < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove items that are not in the cart",
                )
            conn.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, payload.count),
            )
    return {"detail": "Item added/removed successfully"}


@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
def retrieve_cart(payload: RetrieveCartRequest):
    """Retrieve all items in the specified cart."""
    # Validate cart existence
    cart = conn.execute("SELECT 1 FROM carts WHERE id = ?", (payload.cart_id,)).fetchone()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    rows = conn.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (payload.cart_id,),
    ).fetchall()

    items = [CartItem(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)