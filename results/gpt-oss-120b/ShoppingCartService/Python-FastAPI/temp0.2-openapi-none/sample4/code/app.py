import os
import uuid
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel, Field

app = FastAPI(title="Shopping Cart Service", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY
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
    init_db()


# ---------- Pydantic models ----------
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


# ---------- Endpoints ----------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    cart_id = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
)
def add_to_cart(payload: AddToCartRequest, db: sqlite3.Connection = Depends(get_db)):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Fetch current count if any
    cur = db.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (payload.cart_id, payload.item_id),
    )
    row = cur.fetchone()
    current_count = row["count"] if row else 0
    new_count = current_count + payload.count

    if new_count < 0:
        raise HTTPException(status_code=400, detail="Resulting item count cannot be negative")

    if new_count == 0:
        # Remove the row if it exists
        db.execute(
            "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        )
    else:
        if row:
            db.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id),
            )
        else:
            db.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, new_count),
            )
    db.commit()
    return {"detail": "Item added or removed successfully"}


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
)
def retrieve_cart(payload: RetrieveCartRequest, db: sqlite3.Connection = Depends(get_db)):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    cur = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (payload.cart_id,),
    )
    items = [CartItem(item_id=row["item_id"], count=row["count"]) for row in cur.fetchall()]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)