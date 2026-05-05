from fastapi import FastAPI, HTTPException, Body
import sqlite3
import uuid
from pydantic import BaseModel, field_validator
from typing import List, Dict, Optional
import os
import logging

app = FastAPI()
logging.basicConfig(level=logging.ERROR)

def get_db():
    db_path = 'db.sqlite3'
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def create_tables():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY
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
    except Exception as e:
        logging.error(f"Failed to create tables: {e}")
        raise
    finally:
        conn.close()

class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int
    count: int

    @field_validator('cart_id')
    def validate_cart_id(cls, v):
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError('Invalid cart_id format')
        return v

    @field_validator('count')
    def validate_count(cls, v):
        if not (-1000 <= v <= 1000):
            raise ValueError('count must be between -1000 and 1000')
        return v

class RetrieveCartRequest(BaseModel):
    cart_id: str

    @field_validator('cart_id')
    def validate_cart_id(cls, v):
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError('Invalid cart_id format')
        return v

class CartItem(BaseModel):
    item_id: int
    count: int

@app.post("/create_cart", status_code=201)
async def create_cart():
    cart_id = str(uuid.uuid4())
    conn = get_db()
    try:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=500, detail="Failed to create cart")
    finally:
        conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
async def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cart not found")

        cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        result = cursor.fetchone()
        current_count = result['count'] if result else 0
        new_count = current_count + count

        if new_count < 0:
            raise HTTPException(status_code=400, detail="Cannot have negative item count")

        if new_count == 0:
            cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        else:
            cursor.execute("""
                INSERT INTO cart_items (cart_id, item_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(cart_id, item_id) DO UPDATE SET count = excluded.count
            """, (cart_id, item_id, new_count))

        # Check total items in cart
        cursor.execute("SELECT COUNT(*) FROM cart_items WHERE cart_id = ?", (cart_id,))
        total_items = cursor.fetchone()[0]
        if total_items > 100:
            raise HTTPException(status_code=400, detail="Cart item limit exceeded")

        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart")
async def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cart not found")

        cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
        items = cursor.fetchall()
        item_list = [{"item_id": row['item_id'], "count": row['count']} for row in items]
    finally:
        conn.close()

    return {"items": item_list}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)