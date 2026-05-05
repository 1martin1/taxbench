import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List

app = FastAPI()

def get_db():
    conn = sqlite3.connect('db.sqlite3')
    return conn

@app.on_event("startup")
def create_tables():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS carts (
            cart_id TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (cart_id, item_id),
            FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

class CreateCartResponse(BaseModel):
    cart_id: str

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

class RetrieveCartRequest(BaseModel):
    cart_id: str

class CartItem(BaseModel):
    item_id: int
    count: int

class RetrieveCartResponse(BaseModel):
    items: List[CartItem]

@app.post("/create_cart", response_model=CreateCartResponse, status_code=201)
def create_cart():
    cart_id = str(uuid.uuid4())
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    finally:
        conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
    existing = cursor.fetchone()

    if existing:
        new_count = existing[0] + count
        if new_count < 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        elif new_count == 0:
            cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        else:
            cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
    else:
        if count < 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, count))

    conn.commit()
    conn.close()
    return Response(status_code=200)

@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
    items = cursor.fetchall()
    conn.close()

    item_list = [{"item_id": item[0], "count": item[1]} for item in items]
    return {"items": item_list}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)