from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3
import uuid

app = FastAPI()

# Security constants
MAX_CARTS = 100
MAX_COUNT_PER_OPERATION = 1000
MAX_ITEM_COUNT = 10000
MAX_ITEMS_RETURNED = 1000

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid request"},
    )

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

class RetrieveCartRequest(BaseModel):
    cart_id: str

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            cart_id TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT,
            item_id INTEGER,
            count INTEGER,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/create_cart", status_code=201)
async def create_cart():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    
    # Check cart count limit
    cursor.execute("SELECT COUNT(*) FROM carts")
    cart_count = cursor.fetchone()[0]
    if cart_count >= MAX_CARTS:
        raise HTTPException(status_code=429, detail="Too many carts")
    
    try:
        cart_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to create cart")
    finally:
        conn.close()
    
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
async def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count
    
    # Validate count magnitude
    if abs(count) > MAX_COUNT_PER_OPERATION:
        raise HTTPException(status_code=400, detail="Invalid count")
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    
    try:
        # Check cart exists
        cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cart not found")

        # Handle item update
        cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        row = cursor.fetchone()

        if row:
            existing_count = row[0]
            new_count = existing_count + count
            
            # Validate new count
            if new_count > MAX_ITEM_COUNT:
                raise HTTPException(status_code=400, detail="Item count exceeds maximum allowed")
                
            if new_count <= 0:
                cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
            else:
                cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
        else:
            if count <= 0:
                raise HTTPException(status_code=400, detail="Invalid request")
            cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, count))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Internal server error") from e
    finally:
        conn.close()

    return {}

@app.post("/retrieve_cart")
async def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    
    try:
        # Check cart exists
        cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cart not found")

        # Get items with size limit
        cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
        rows = cursor.fetchall()
        
        items = [{"item_id": row[0], "count": row[1]} for row in rows]
        
        if len(items) > MAX_ITEMS_RETURNED:
            raise HTTPException(status_code=413, detail="Too many items in cart")
    finally:
        conn.close()

    return {"items": items}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)