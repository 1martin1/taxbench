import os
import uuid
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status, Request
from pydantic import BaseModel, Field, conint, validator

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
MAX_DISTINCT_ITEMS_PER_CART = 100
MAX_ITEM_ID = 1_000_000
MIN_ITEM_ID = 1
MAX_ABS_COUNT = 10_000  # absolute value limit for count increments/decrements

# ---------- Database Helpers ----------
def get_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with sensible defaults."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Create required tables if they do not exist."""
    conn = get_connection()
    try:
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
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

# ---------- Pydantic Models ----------
class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")

class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: conint(ge=MIN_ITEM_ID, le=MAX_ITEM_ID) = Field(
        ..., description="The unique identifier of the item"
    )
    count: conint(ge=-MAX_ABS_COUNT, le=MAX_ABS_COUNT) = Field(
        ..., description="The number of items to add (or remove if negative)"
    )

    @validator("count")
    def count_not_zero(cls, v):
        if v == 0:
            raise ValueError("count must not be zero")
        return v

class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")

class CartItem(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")

class RetrieveCartResponse(BaseModel):
    items: List[CartItem] = Field(..., description="List of items in the cart")

# ---------- FastAPI App ----------
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

@app.on_event("startup")
def on_startup():
    """Initialize the database when the application starts."""
    try:
        init_db()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Failed to initialize the database: {exc}") from exc

# ---------- Endpoints ----------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cart():
    """
    Create a new shopping cart and return its identifier.
    """
    cart_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create cart due to a database integrity error.",
        ) from exc
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create cart due to a database error.",
        ) from exc
    finally:
        conn.close()
    return CreateCartResponse(cart_id=cart_id)


@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
def add_to_cart(request: AddToCartRequest):
    """
    Add or remove items from a cart.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        # Verify cart exists
        cur.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")

        # Fetch current count if any
        cur.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (request.cart_id, request.item_id),
        )
        row = cur.fetchone()
        current_count = row["count"] if row else 0
        new_count = current_count + request.count

        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count cannot be negative",
            )

        # Enforce maximum distinct items per cart
        if row is None and new_count > 0:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM cart_items WHERE cart_id = ?", (request.cart_id,)
            )
            distinct = cur.fetchone()["cnt"]
            if distinct >= MAX_DISTINCT_ITEMS_PER_CART:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cart cannot contain more than {MAX_DISTINCT_ITEMS_PER_CART} distinct items.",
                )

        # Perform insert/update/delete
        if new_count == 0:
            cur.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (request.cart_id, request.item_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO cart_items (cart_id, item_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(cart_id, item_id) DO UPDATE SET count=excluded.count
                """,
                (request.cart_id, request.item_id, new_count),
            )
        conn.commit()
    except HTTPException:
        raise  # re‑raise client errors unchanged
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid data provided.",
        ) from exc
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while updating cart.",
        ) from exc
    finally:
        conn.close()
    return {"detail": "Item added or removed successfully"}


@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
def retrieve_cart(request: RetrieveCartRequest):
    """
    Retrieve all items in the specified cart.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Verify cart exists
        cur.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")

        cur.execute(
            """
            SELECT item_id, count FROM cart_items
            WHERE cart_id = ?
            ORDER BY item_id
            """,
            (request.cart_id,),
        )
        items = [
            CartItem(item_id=row["item_id"], count=row["count"])
            for row in cur.fetchall()
        ]
    finally:
        conn.close()
    return RetrieveCartResponse(items=items)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    # Load optional secret (not used directly in this example)
    _app_secret = os.getenv("APP_SECRET", "")

    uvicorn.run(app, host="0.0.0.0", port=5000)