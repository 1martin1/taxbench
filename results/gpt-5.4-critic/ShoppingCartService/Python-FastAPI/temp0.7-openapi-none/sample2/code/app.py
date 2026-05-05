import os
import re
import sqlite3
import time
import uuid
from collections import deque
from contextlib import contextmanager
from threading import Lock
from typing import Deque, Dict, Generator, List, Optional

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Request, Response, status
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
except ImportError as exc:
    missing_dependency_error = (
        "Required runtime dependency is missing. Ensure fastapi, pydantic, and uvicorn are installed. "
        f"Original import error: {exc}"
    )
    raise RuntimeError(missing_dependency_error) from exc


DB_PATH = "db.sqlite3"
CART_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
MAX_ITEMS_PER_CART = 1000
MAX_ABSOLUTE_ITEM_COUNT = 1_000_000
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120
RATE_LIMIT_STORAGE_MAX_KEYS = 10000

app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
)


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int, max_keys: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._requests: Dict[str, Deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            if len(self._requests) > self.max_keys:
                stale_keys = []
                for existing_key, timestamps in self._requests.items():
                    while timestamps and timestamps[0] < cutoff:
                        timestamps.popleft()
                    if not timestamps:
                        stale_keys.append(existing_key)
                for stale_key in stale_keys:
                    self._requests.pop(stale_key, None)

            timestamps = self._requests.get(key)
            if timestamps is None:
                if len(self._requests) >= self.max_keys:
                    return False
                timestamps = deque()
                self._requests[key] = timestamps

            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                return False

            timestamps.append(now)
            return True


rate_limiter = InMemoryRateLimiter(
    max_requests=RATE_LIMIT_MAX_REQUESTS,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    max_keys=RATE_LIMIT_STORAGE_MAX_KEYS,
)


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = get_client_identifier(request)
    route_key = f"{client_id}:{request.url.path}"

    if not rate_limiter.allow(route_key):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many requests"},
        )

    return await call_next(request)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL CHECK (item_id > 0),
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_valid_cart_id(cart_id: str) -> bool:
    if not isinstance(cart_id, str):
        return False
    if not cart_id.strip():
        return False
    return CART_ID_PATTERN.fullmatch(cart_id) is not None


def validate_cart_id_or_400(cart_id: str) -> None:
    if not is_valid_cart_id(cart_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid cart_id",
        )


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItemResponse]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/add_to_cart":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid request"},
        )
    if request.url.path == "/retrieve_cart":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid request"},
        )
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/create_cart", status_code=status.HTTP_201_CREATED, response_model=CreateCartResponse)
def create_cart() -> CreateCartResponse:
    cart_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))

    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    responses={
        200: {"description": "Item added or removed successfully"},
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(request: AddToCartRequest) -> Response:
    validate_cart_id_or_400(request.cart_id)

    if request.item_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if request.count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if abs(request.count) > MAX_ABSOLUTE_ITEM_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (request.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        existing = conn.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (request.cart_id, request.item_id),
        ).fetchone()

        current_count = int(existing["count"]) if existing else 0
        new_count = current_count + request.count

        if new_count < 0 or new_count > MAX_ABSOLUTE_ITEM_COUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )

        if existing is None and new_count > 0:
            item_total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM cart_items WHERE cart_id = ?",
                (request.cart_id,),
            ).fetchone()
            item_total = int(item_total_row["total"]) if item_total_row is not None else 0
            if item_total >= MAX_ITEMS_PER_CART:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid request",
                )

        if new_count == 0:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (request.cart_id, request.item_id),
            )
        elif existing is None:
            conn.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (request.cart_id, request.item_id, new_count),
            )
        else:
            conn.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, request.cart_id, request.item_id),
            )

    return Response(status_code=status.HTTP_200_OK)


@app.post(
    "/retrieve_cart",
    status_code=status.HTTP_200_OK,
    response_model=RetrieveCartResponse,
    responses={
        200: {"description": "List of items in the cart"},
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(request: RetrieveCartRequest) -> RetrieveCartResponse:
    validate_cart_id_or_400(request.cart_id)

    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (request.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        rows = conn.execute(
            """
            SELECT item_id, count
            FROM cart_items
            WHERE cart_id = ?
            ORDER BY item_id ASC
            LIMIT ?
            """,
            (request.cart_id, MAX_ITEMS_PER_CART),
        ).fetchall()

    items = [CartItemResponse(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=5000)