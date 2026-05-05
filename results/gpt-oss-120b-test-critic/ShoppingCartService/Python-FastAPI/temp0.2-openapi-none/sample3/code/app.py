import os
import uuid
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, Field

# FastAPI application
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

# Database file path
DB_PATH = "db.sqlite3"


def _init_db() -> None:
    """Create database tables if they do not exist."""
    with sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
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


@app.on_event("startup")
def on_startup():
    _init_db()


def get_db():
    """
    Dependency that provides a fresh SQLite connection per request.
    The connection is closed automatically after the request is processed.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# Pydantic models
class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItem(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItem] = Field(..., description="List of items in the cart")


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    """Create a new shopping cart and return its identifier."""
    cart_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=500, detail="Failed to create cart") from exc
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
)
def add_to_cart(request: AddToCartRequest, db: sqlite3.Connection = Depends(get_db)):
    """Add or remove items from a cart."""
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Get current count for the item
    cur = db.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (request.cart_id, request.item_id),
    )
    row = cur.fetchone()
    current_count = row["count"] if row else 0
    new_count = current_count + request.count

    if new_count < 0:
        raise HTTPException(status_code=400, detail="Resulting item count cannot be negative")

    if new_count == 0:
        # Remove the item entry if count drops to zero
        db.execute(
            "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (request.cart_id, request.item_id),
        )
    else:
        if row:
            db.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, request.cart_id, request.item_id),
            )
        else:
            db.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (request.cart_id, request.item_id, new_count),
            )
    db.commit()
    return {"detail": "Item added or removed successfully"}


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
)
def retrieve_cart(request: RetrieveCartRequest, db: sqlite3.Connection = Depends(get_db)):
    """Retrieve all items in the specified cart."""
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    cur = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (request.cart_id,),
    )
    items = [
        CartItem(item_id=row["item_id"], count=row["count"])
        for row in cur.fetchall()
    ]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)