import os
import uuid
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status, Depends, Header
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")

# ----------------------------------------------------------------------
# FastAPI application
# ----------------------------------------------------------------------
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with foreign keys enforcement."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initialize the required tables."""
    conn = get_connection()
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
    conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def db_conn():
    """Dependency that yields a DB connection per request."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Security
# ----------------------------------------------------------------------
def verify_token(authorization: str = Header(..., description="Bearer token")) -> None:
    """
    Simple token authentication. The client must send:
        Authorization: Bearer <APP_SECRET>
    """
    if not APP_SECRET:
        # If no secret is configured, deny all requests for safety.
        raise HTTPException(status_code=401, detail="Authentication secret not configured")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != APP_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_token)],
)
def create_cart(conn: sqlite3.Connection = Depends(db_conn)):
    """Create a new shopping cart and return its identifier."""
    cart_id = str(uuid.uuid4())
    try:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_token)],
)
def add_to_cart(
    request: AddToCartRequest,
    conn: sqlite3.Connection = Depends(db_conn),
):
    """Add or remove items from a cart."""
    # Verify cart exists
    cur = conn.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Get current count for the item
    cur = conn.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (request.cart_id, request.item_id),
    )
    row = cur.fetchone()
    current_count = row["count"] if row else 0
    new_count = current_count + request.count

    if new_count < 0:
        raise HTTPException(
            status_code=400, detail="Resulting item count cannot be negative"
        )

    if new_count == 0:
        # Remove the item entry if count drops to zero
        conn.execute(
            "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (request.cart_id, request.item_id),
        )
    else:
        if row:
            # Update existing row
            conn.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, request.cart_id, request.item_id),
            )
        else:
            # Insert new row
            conn.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (request.cart_id, request.item_id, new_count),
            )
    conn.commit()
    return {"detail": "Item added or removed successfully"}


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    dependencies=[Depends(verify_token)],
)
def retrieve_cart(
    request: RetrieveCartRequest,
    conn: sqlite3.Connection = Depends(db_conn),
):
    """Retrieve all items in the specified cart."""
    # Verify cart exists
    cur = conn.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    cur = conn.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (request.cart_id,),
    )
    items = [
        CartItem(item_id=row["item_id"], count=row["count"])
        for row in cur.fetchall()
    ]
    return RetrieveCartResponse(items=items)


# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)