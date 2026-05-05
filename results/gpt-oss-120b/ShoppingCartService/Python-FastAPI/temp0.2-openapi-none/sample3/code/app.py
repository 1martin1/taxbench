import os
import uuid
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, Field

# Initialize FastAPI app
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

# Database connection (shared, thread‑safe)
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Create tables if they don't exist
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS carts (
        cart_id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
)
cursor.execute(
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


# Dependency to get a fresh cursor (optional, here we reuse the global connection)
def get_db():
    try:
        yield conn
    finally:
        pass  # connection stays open for the life of the app


@app.post("/create_cart", response_model=CreateCartResponse, status_code=status.HTTP_201_CREATED)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    """Create a new shopping cart and return its identifier."""
    cart_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    return CreateCartResponse(cart_id=cart_id)


@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
def add_to_cart(request: AddToCartRequest, db: sqlite3.Connection = Depends(get_db)):
    """Add or remove items from a cart."""
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Fetch current count if any
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
        # Remove the row if count drops to zero
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


@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
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
    items = [CartItem(item_id=row["item_id"], count=row["count"]) for row in cur.fetchall()]
    return RetrieveCartResponse(items=items)


# Optional: expose the secret (not used in this simple service)
APP_SECRET = os.getenv("APP_SECRET", "")

# Run the application with uvicorn when executed directly
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)