import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timedelta

app = FastAPI()

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int, window_seconds: int):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}

    async def dispatch(self, request, call_next):
        client_host = request.client.host
        now = datetime.now()

        if client_host in self.requests:
            self.requests[client_host] = [t for t in self.requests[client_host] if (now - t).total_seconds() < self.window_seconds]

        if len(self.requests.get(client_host, [])) >= self.max_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        self.requests.setdefault(client_host, []).append(now)
        response = await call_next(request)
        return response

app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request"},
    )

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS carts (cart_id TEXT PRIMARY KEY)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int = Field(..., ge=-1000, le=1000)

class RetrieveCartRequest(BaseModel):
    cart_id: str

class CartItem(BaseModel):
    item_id: int
    count: int

class RetrieveCartResponse(BaseModel):
    items: List[CartItem]

@app.post("/create_cart", status_code=201)
async def create_cart():
    cart_id = str(uuid.uuid4())
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail="Failed to create cart")
    finally:
        conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
async def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            conn.rollback()
            raise HTTPException(status_code=404, detail="Cart not found")

        cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        existing = cursor.fetchone()

        if existing:
            new_count = existing[0] + count
            if new_count <= 0:
                cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
            else:
                cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
        else:
            if count <= 0:
                conn.rollback()
                raise HTTPException(status_code=400, detail="Invalid request")
            cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, count))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart")
async def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id

    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            conn.rollback()
            raise HTTPException(status_code=404, detail="Cart not found")

        cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
        items = cursor.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")
    finally:
        conn.close()

    return {"items": [{"item_id": item[0], "count": item[1]} for item in items]}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)