import hmac
import hashlib
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Generator, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
MAX_CART_ID_LENGTH = 128
MAX_ITEM_ID = 2_147_483_647
MAX_COUNT_ABS = 1_000_000_000


def get_app_secret() -> str:
    secret = os.getenv("APP_SECRET")
    if secret:
        return secret
    return "default-development-secret-change-me"


def generate_cart_id() -> str:
    return str(uuid.uuid4())


def generate_cart_token(cart_id: str) -> str:
    secret = get_app_secret().encode("utf-8")
    return hmac.new(secret, cart_id.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_cart_token(cart_id: str, token: Optional[str]) -> bool:
    if not token:
        return False
    expected = generate_cart_token(cart_id)
    return hmac.compare_digest(expected, token)


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


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
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

    cart_id: str = Field(..., description="The unique identifier of the cart", min_length=1, max_length=MAX_CART_ID_LENGTH)
    item_id: int = Field(..., description="The unique identifier of the item", ge=1, le=MAX_ITEM_ID)
    count: int = Field(..., description="The number of items to add (or remove if negative)", ge=-MAX_COUNT_ABS, le=MAX_COUNT_ABS)

    @field_validator("cart_id")
    @classmethod
    def validate_cart_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("invalid request")
        return value


class RetrieveCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart", min_length=1, max_length=MAX_CART_ID_LENGTH)

    @field_validator("cart_id")
    @classmethod
    def validate_cart_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("invalid request")
        return value


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: list[CartItemResponse]


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def require_cart_access(cart_id: str, x_cart_token: Optional[str]) -> None:
    if not verify_cart_token(cart_id, x_cart_token):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found",
        )


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createCart",
    summary="Create a new shopping cart",
)
def create_cart(response: Response) -> CreateCartResponse:
    cart_id = generate_cart_id()
    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    response.headers["X-Cart-Token"] = generate_cart_token(cart_id)
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    operation_id="addToCart",
    summary="Add or remove items from the cart",
    responses={
        200: {"description": "Item added or removed successfully"},
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(
    payload: AddToCartRequest,
    x_cart_token: Optional[str] = Header(default=None, alias="X-Cart-Token"),
) -> Response:
    require_cart_access(payload.cart_id, x_cart_token)

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
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        ).fetchone()

        current_count = existing["count"] if existing is not None else 0
        new_count = current_count + payload.count

        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )

        if new_count == 0:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id),
            )
        elif existing is None:
            conn.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, new_count),
            )
        else:
            conn.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id),
            )

    return Response(status_code=status.HTTP_200_OK)


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    operation_id="retrieveCart",
    summary="Retrieve all items in the cart",
    responses={
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(
    payload: RetrieveCartRequest,
    x_cart_token: Optional[str] = Header(default=None, alias="X-Cart-Token"),
) -> RetrieveCartResponse:
    require_cart_access(payload.cart_id, x_cart_token)

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

    items = [CartItemResponse(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)