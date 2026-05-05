import os
import uuid
import sqlite3
from typing import List, Generator

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

from starlette.concurrency import run_in_threadpool

# Constants
DB_PATH = "db.sqlite3"
MAX_ABSOLUTE_COUNT = 10_000  # Upper bound for item count modifications
MAX_ITEMS_RETURNED = 10_000  # Upper bound for items returned in retrieve_cart

# FastAPI app
app = FastAPI(title="Shopping Cart Service", version="1.0.0")

# -----------------------------------------------------------
# Database utilities
# -----------------------------------------------------------
def init_db() -> None:
    """
    Initialise the SQLite database and required tables.
    This function is called during application startup.
    """
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
        conn.close()
    except Exception as exc:
        # If the DB cannot be created, the application should fail fast.
        raise RuntimeError(f"Failed to initialise the database: {exc}") from exc


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Dependency that provides a fresh SQLite connection per request.
    The connection is closed after the request is processed.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    try:
        yield conn
    finally:
        conn.close()


# -----------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------
class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")

    @validator("count")
    def validate_count(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Count cannot be zero")
        if abs(v) > MAX_ABSOLUTE_COUNT:
            raise ValueError(f"Absolute count must not exceed {MAX_ABSOLUTE_COUNT}")
        return v


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItem(BaseModel):
    item_id: int
    count: int


class RetrieveCartResponse(BaseModel):
    items: List[CartItem]


# -----------------------------------------------------------
# Application lifecycle events
# -----------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    # Initialise the database; any exception will stop the app from starting.
    init_db()
    # Ensure APP_SECRET is provided; avoid using a predictable default.
    if not os.getenv("APP_SECRET"):
        # Not raising an error because the secret is not used in this example,
        # but we avoid setting an insecure default.
        os.environ["APP_SECRET"] = ""


# -----------------------------------------------------------
# Endpoint implementations (async)
# -----------------------------------------------------------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cart(db: sqlite3.Connection = Depends(get_db)):
    cart_id = str(uuid.uuid4())

    async def _insert():
        cur = db.cursor()
        cur.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        db.commit()

    try:
        await run_in_threadpool(_insert)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=500, detail="Failed to create cart") from exc

    return CreateCartResponse(cart_id=cart_id)


@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
async def add_to_cart(
    request: AddToCartRequest, db: sqlite3.Connection = Depends(get_db)
):
    async def _process():
        cur = db.cursor()
        # Verify cart exists
        cur.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Cart not found")

        # Check existing item count
        cur.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (request.cart_id, request.item_id),
        )
        row = cur.fetchone()
        if row:
            new_count = row[0] + request.count
            if new_count > 0:
                cur.execute(
                    "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                    (new_count, request.cart_id, request.item_id),
                )
            else:
                # Remove the item if count drops to zero or negative
                cur.execute(
                    "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                    (request.cart_id, request.item_id),
                )
        else:
            if request.count < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove items that are not in the cart",
                )
            cur.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (request.cart_id, request.item_id, request.count),
            )
        db.commit()

    await run_in_threadpool(_process)
    return JSONResponse(content={"detail": "Item added or removed successfully"})


@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
async def retrieve_cart(
    request: RetrieveCartRequest, db: sqlite3.Connection = Depends(get_db)
):
    async def _fetch():
        cur = db.cursor()
        # Verify cart exists
        cur.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Cart not found")

        cur.execute(
            "SELECT item_id, count FROM cart_items WHERE cart_id = ? LIMIT ?",
            (request.cart_id, MAX_ITEMS_RETURNED),
        )
        rows = cur.fetchall()
        return [CartItem(item_id=row[0], count=row[1]) for row in rows]

    items = await run_in_threadpool(_fetch)
    return RetrieveCartResponse(items=items)


# -----------------------------------------------------------
# Run the application
# -----------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)