import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, model_validator
from typing import Dict, Any
from fastapi.middleware.base import BaseHTTPMiddleware
import time
from starlette.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

app = FastAPI()

MAX_CARTS = 1000
MAX_ITEMS_PER_CART = 100
MAX_COUNT_DELTA = 100  # Max absolute value for count parameter

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

    @model_validator(mode='before')
    @classmethod
    def validate_count(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        count = values.get('count')
        if count is not None and abs(count) > MAX_COUNT_DELTA:
            raise ValueError(f"Count must be between -{MAX_COUNT_DELTA} and {MAX_COUNT_DELTA}")
        return values

class RetrieveCartRequest(BaseModel):
    cart_id: str

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests=100, window=60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self.request_counts = {}

    async def dispatch(self, request: Request, call_next):
        client_host = request.client.host
        now = time.time()

        # Clean up old entries
        to_remove = [host for host in self.request_counts 
                    if now - self.request_counts[host]['timestamp'] > self.window]
        for host in to_remove:
            del self.request_counts[host]

        if client_host in self.request_counts:
            if self.request_counts[client_host]['count'] >= self.max_requests:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
            self.request_counts[client_host]['count'] += 1
        else:
            self.request_counts[client_host] = {'count': 1, 'timestamp': now}

        response = await call_next(request)
        return response

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    raise HTTPException(status_code=400, detail="Invalid request")

@app.on_event("startup")
def startup():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS carts (
            cart_id TEXT PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT,
            item_id INTEGER,
            count INTEGER,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

@app.post("/create_cart", status_code=201)
def create_cart():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    
    # Check cart limit
    cursor.execute("SELECT COUNT(*) FROM carts")
    total_carts = cursor.fetchone()[0]
    if total_carts >= MAX_CARTS:
        conn.close()
        raise HTTPException(status_code=400, detail="Maximum number of carts reached")
    
    cart_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    conn.commit()
    conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Get current item count
    cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
    result = cursor.fetchone()

    if result:
        new_count = result[0] + count
        cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
    else:
        # Check item limit before adding new item
        cursor.execute("SELECT COUNT(*) FROM cart_items WHERE cart_id = ?", (cart_id,))
        current_items = cursor.fetchone()[0]
        if current_items >= MAX_ITEMS_PER_CART:
            conn.close()
            raise HTTPException(status_code=400, detail="Maximum items per cart reached")
            
        new_count = count
        cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, new_count))

    if new_count == 0:
        cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))

    conn.commit()
    conn.close()

    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart")
def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id

    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
    items = [{"item_id": row[0], "count": row[1]} for row in cursor.fetchall()]
    conn.close()

    return {"items": items}

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware, max_requests=100, window=60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)