import os
import uuid
import sqlite3
from contextlib import contextmanager
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# Configuration constants
DB_PATH = "db.sqlite3"
MAX_COUNT_PER_REQUEST = 1_000          # Absolute value limit for a single operation
MAX_TOTAL_COUNT_PER_ITEM = 10_000      # Upper bound for total count of a single item in a cart

# FastAPI application with lifespan handler for startup/teardown
app = FastAPI(title="Shopping Cart Service", version="1.0.0")


def init_db() -> None:
    """Create required tables if they do not exist."""
    with sqlite3.connect(DB_PATH) as conn:
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


# Lifespan event to initialize the database once when the app starts
@app.on_event("startup")
def on_startup() -> None:
    init_db()


@contextmanager
def get_db():
    """Provide a per‑request SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# Pydantic models
class CreateCartResponse(BaseModel):
    cart_id: str


class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int = Field(..., description="Number of items to add (positive) or remove (negative)")

    @validator("count")
    def count_within_limits(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Count cannot be zero")
        if abs(v) > MAX_COUNT_PER_REQUEST:
            raise ValueError(f"Count magnitude exceeds allowed limit of {MAX_COUNT_PER_REQUEST}")
        return v


class RetrieveCartRequest(BaseModel):
    cart_id: str


class CartItem(BaseModel):
    item_id: int
    count: int


class RetrieveCartResponse(BaseModel):
    items: List[CartItem]


# Endpoint implementations
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    """Create a new shopping cart and return its identifier."""
    cart_id = str(uuid.uuid4())
    try:
        with db:
            db.execute("INSERT INTO carts (id) VALUES (?)", (cart_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    return CreateCartResponse(cart_id=cart_id)


@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
def add_to_cart(
    payload: AddToCartRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """Add or remove items from a cart."""
    # Verify cart existence
    cart = db.execute("SELECT 1 FROM carts WHERE id = ?", (payload.cart_id,)).fetchone()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    with db:
        existing = db.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        ).fetchone()

        if existing:
            new_count = existing["count"] + payload.count
            if new_count > 0:
                if new_count > MAX_TOTAL_COUNT_PER_ITEM:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Resulting item count exceeds maximum allowed ({MAX_TOTAL_COUNT_PER_ITEM})",
                    )
                db.execute(
                    "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                    (new_count, payload.cart_id, payload.item_id),
                )
            else:
                # Remove the item if count drops to zero or below
                db.execute(
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
            if payload.count > MAX_TOTAL_COUNT_PER_ITEM:
                raise HTTPException(
                    status_code=400,
                    detail=f"Item count exceeds maximum allowed ({MAX_TOTAL_COUNT_PER_ITEM})",
                )
            db.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, payload.count),
            )
    return JSONResponse(content={"detail": "Item added/removed successfully"})


@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
def retrieve_cart(
    payload: RetrieveCartRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """Retrieve all items in the specified cart."""
    # Verify cart existence
    cart = db.execute("SELECT 1 FROM carts WHERE id = ?", (payload.cart_id,)).fetchone()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    rows = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (payload.cart_id,),
    ).fetchall()

    items = [CartItem(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)