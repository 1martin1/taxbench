import hmac
import hashlib
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Generator, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
MAX_INT_32 = 2_147_483_647
APP_SECRET = os.environ.get("APP_SECRET", "")


def _require_app_secret() -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable must be set")
    return APP_SECRET


def _build_cart_token(cart_id: str) -> str:
    secret = _require_app_secret().encode("utf-8")
    return hmac.new(secret, cart_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_cart_access(cart_id: str, token: Optional[str]) -> None:
    expected = _build_cart_token(cart_id)
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL CHECK (item_id >= 1 AND item_id <= 2147483647),
                count INTEGER NOT NULL CHECK (count >= 1 AND count <= 2147483647),
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
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: int) -> int:
        if value < 1 or value > MAX_INT_32:
            raise ValueError(f"item_id must be between 1 and {MAX_INT_32}")
        return value

    @field_validator("count")
    @classmethod
    def validate_count(cls, value: int) -> int:
        if value == 0:
            raise ValueError("count must not be zero")
        if value < -MAX_INT_32 or value > MAX_INT_32:
            raise ValueError(f"count must be between {-MAX_INT_32} and {MAX_INT_32}, excluding zero")
        return value


class RetrieveCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItem(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItem]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _require_app_secret()
    yield


app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart(response: Response) -> CreateCartResponse:
    cart_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    response.headers["X-Cart-Token"] = _build_cart_token(cart_id)
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
    responses={
        200: {"description": "Item added or removed successfully"},
        400: {"description": "Invalid request"},
        403: {"description": "Forbidden"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(
    payload: AddToCartRequest,
    x_cart_token: Optional[str] = Header(default=None, alias="X-Cart-Token"),
) -> Response:
    _verify_cart_access(payload.cart_id, x_cart_token)

    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (payload.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        existing = conn.execute(
            """
            SELECT count FROM cart_items
            WHERE cart_id = ? AND item_id = ?
            """,
            (payload.cart_id, payload.item_id),
        ).fetchone()

        current_count = int(existing["count"]) if existing is not None else 0
        new_count = current_count + payload.count

        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count cannot be negative",
            )

        if new_count > MAX_INT_32:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Resulting item count must not exceed {MAX_INT_32}",
            )

        if new_count == 0:
            conn.execute(
                """
                DELETE FROM cart_items
                WHERE cart_id = ? AND item_id = ?
                """,
                (payload.cart_id, payload.item_id),
            )
        elif existing is None:
            conn.execute(
                """
                INSERT INTO cart_items (cart_id, item_id, count)
                VALUES (?, ?, ?)
                """,
                (payload.cart_id, payload.item_id, new_count),
            )
        else:
            conn.execute(
                """
                UPDATE cart_items
                SET count = ?
                WHERE cart_id = ? AND item_id = ?
                """,
                (new_count, payload.cart_id, payload.item_id),
            )

    return Response(status_code=status.HTTP_200_OK)


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
    responses={
        403: {"description": "Forbidden"},
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(
    payload: RetrieveCartRequest,
    x_cart_token: Optional[str] = Header(default=None, alias="X-Cart-Token"),
) -> RetrieveCartResponse:
    _verify_cart_access(payload.cart_id, x_cart_token)

    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (payload.cart_id,),
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
            """,
            (payload.cart_id,),
        ).fetchall()

    items = [CartItem(item_id=int(row["item_id"]), count=int(row["count"])) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    _require_app_secret()
    uvicorn.run(app, host="0.0.0.0", port=5000)