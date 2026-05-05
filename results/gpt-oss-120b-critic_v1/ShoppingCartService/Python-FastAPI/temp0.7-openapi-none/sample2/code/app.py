import os
import sqlite3
import uuid
import time
from typing import List

from fastapi import FastAPI, HTTPException, status, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB
RATE_LIMIT_MAX_REQUESTS = 20      # max requests
RATE_LIMIT_PERIOD = 60            # per 60 seconds

# ---------- Database utilities ----------
def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # Enable foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()
        # Table for carts (only stores the cart identifier)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY
            )
            """
        )
        # Table for items in each cart
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

# ---------- Security dependencies ----------
def verify_secret(x_app_secret: str = Header(None)):
    """
    Simple token authentication using the APP_SECRET environment variable.
    Clients must provide the secret in the X-APP-SECRET header.
    """
    expected = os.getenv("APP_SECRET")
    if not expected or x_app_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return True

# ---------- Middleware ----------
class LimitBodySizeMiddleware(BaseHTTPMiddleware):
    """
    Reject requests with a payload larger than MAX_BODY_SIZE.
    """
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_SIZE:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Payload too large"},
            )
        return await call_next(request)

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in‑memory rate limiting per client IP.
    """
    def __init__(self, app, max_requests: int, period: int):
        super().__init__(app)
        self.max_requests = max_requests
        self.period = period
        self.clients = {}  # type: dict[str, List[float]]

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "anonymous"
        now = time.time()
        timestamps = self.clients.get(client_ip, [])
        # Keep only timestamps within the period
        timestamps = [ts for ts in timestamps if now - ts < self.period]
        if len(timestamps) >= self.max_requests:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too Many Requests"},
            )
        timestamps.append(now)
        self.clients[client_ip] = timestamps
        return await call_next(request)

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
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")

class RetrieveCartResponse(BaseModel):
    items: List[CartItem] = Field(..., description="List of items in the cart")

# ---------- FastAPI app ----------
app = FastAPI(title="Shopping Cart Service", version="1.0.0")
app.add_middleware(LimitBodySizeMiddleware)
app.add_middleware(RateLimitMiddleware, max_requests=RATE_LIMIT_MAX_REQUESTS, period=RATE_LIMIT_PERIOD)

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Endpoints ----------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_secret)],
)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    """
    Create a new shopping cart and return its identifier.
    """
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
    dependencies=[Depends(verify_secret)],
)
def add_to_cart(
    payload: AddToCartRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Add or remove items from the cart.
    """
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    # Fetch existing count for the item, if any
    cur = db.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (payload.cart_id, payload.item_id),
    )
    row = cur.fetchone()
    existing_count = row["count"] if row else 0
    new_count = existing_count + payload.count

    if new_count < 0:
        raise HTTPException(status_code=400, detail="Resulting item count cannot be negative")

    if row:
        if new_count == 0:
            db.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id),
            )
        else:
            db.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id),
            )
    else:
        if payload.count <= 0:
            raise HTTPException(status_code=400, detail="Cannot add non‑positive count for a new item")
        db.execute(
            "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
            (payload.cart_id, payload.item_id, payload.count),
        )
    db.commit()
    return {"detail": "Item added or removed successfully"}


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_secret)],
)
def retrieve_cart(
    payload: RetrieveCartRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Retrieve all items in the specified cart.
    """
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (payload.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Cart not found")

    cur = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (payload.cart_id,),
    )
    items = [
        CartItem(item_id=row["item_id"], count=row["count"])
        for row in cur.fetchall()
    ]
    return RetrieveCartResponse(items=items)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    # Ensure APP_SECRET exists even if not used elsewhere
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)