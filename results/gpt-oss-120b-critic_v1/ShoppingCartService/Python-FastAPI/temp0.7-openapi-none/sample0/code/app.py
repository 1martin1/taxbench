import os
import uuid
import sqlite3
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, conint, validator
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_ITEM_COUNT = 1_000      # Maximum absolute count per item
MAX_ITEMS_PER_CART = 1_000  # Maximum distinct items per cart
MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1 MB


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Shopping Cart Service", version="1.0.0")


# ---------------------------------------------------------------------------
# Middleware to limit request size
# ---------------------------------------------------------------------------
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        if len(body) > MAX_REQUEST_SIZE:
            return JSONResponse(
                {"detail": "Request body too large"},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        # Re‑inject the body for downstream handlers
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive  # type: ignore
        return await call_next(request)

app.add_middleware(RequestSizeLimitMiddleware)


# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db():
    """Provide a thread‑local SQLite connection."""
    conn = sqlite3.connect("db.sqlite3")
    conn.row_factory = sqlite3.Row
    # Enforce foreign‑key constraints
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create required tables if they do not exist."""
    with sqlite3.connect("db.sqlite3") as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
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


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------------------------------------------------------------------
# Pydantic models with validation
# ---------------------------------------------------------------------------
class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: conint(gt=0) = Field(..., description="The unique identifier of the item (positive integer)")
    count: conint(ge=-MAX_ITEM_COUNT, le=MAX_ITEM_COUNT) = Field(
        ..., description=f"The number of items to add (or remove if negative, absolute value ≤ {MAX_ITEM_COUNT})"
    )

    @validator("count")
    def count_not_zero(cls, v):
        if v == 0:
            raise ValueError("count cannot be zero")
        return v


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItemResponse] = Field(..., description="List of items in the cart")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    cart_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    try:
        db.execute(
            "INSERT INTO carts (cart_id, created_at) VALUES (?, ?)",
            (cart_id, created_at),
        )
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
def add_to_cart(
    payload: AddToCartRequest, db: sqlite3.Connection = Depends(get_db)
):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Ensure the cart does not exceed the maximum distinct items
    cur = db.execute(
        "SELECT COUNT(*) AS cnt FROM cart_items WHERE cart_id = ?", (payload.cart_id,)
    )
    distinct_items = cur.fetchone()["cnt"]
    if distinct_items >= MAX_ITEMS_PER_CART and not _item_exists(db, payload.cart_id, payload.item_id):
        raise HTTPException(
            status_code=400,
            detail=f"Cart cannot contain more than {MAX_ITEMS_PER_CART} distinct items",
        )

    # Process addition / removal
    cur = db.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (payload.cart_id, payload.item_id),
    )
    row = cur.fetchone()

    if row:
        new_count = row["count"] + payload.count
        if new_count > 0:
            if abs(new_count) > MAX_ITEM_COUNT:
                raise HTTPException(
                    status_code=400,
                    detail=f"Resulting item count exceeds allowed maximum of {MAX_ITEM_COUNT}",
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
        if payload.count < 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove items that are not in the cart",
            )
        # Insert new item
        db.execute(
            "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
            (payload.cart_id, payload.item_id, payload.count),
        )
    db.commit()
    return {"detail": "Item added or removed successfully"}


def _item_exists(db: sqlite3.Connection, cart_id: str, item_id: int) -> bool:
    cur = db.execute(
        "SELECT 1 FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id)
    )
    return cur.fetchone() is not None


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
)
def retrieve_cart(
    payload: RetrieveCartRequest, db: sqlite3.Connection = Depends(get_db)
):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    cur = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (payload.cart_id,),
    )
    items = [
        CartItemResponse(item_id=row["item_id"], count=row["count"])
        for row in cur.fetchall()
    ]
    return RetrieveCartResponse(items=items)


# ---------------------------------------------------------------------------
# Run the application
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)