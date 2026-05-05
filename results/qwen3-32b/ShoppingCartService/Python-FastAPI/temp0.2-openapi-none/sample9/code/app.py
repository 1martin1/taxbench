from fastapi import FastAPI, Request, HTTPException
import sqlite3
import uuid

app = FastAPI()

def get_db():
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

@app.on_event("startup")
def create_tables():
    conn = get_db()
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

@app.post("/create_cart", status_code=201)
async def create_cart():
    cart_id = str(uuid.uuid4())
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
async def add_to_cart(request: Request):
    try:
        data = await request.json()
        cart_id = data["cart_id"]
        item_id = data["item_id"]
        count = data["count"]
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid request")
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Cart not found")
        
        cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        existing = cursor.fetchone()
        
        if existing:
            existing_count = existing[0]
            new_count = existing_count + count
            if new_count < 0:
                raise HTTPException(status_code=400, detail="Invalid request")
            if new_count == 0:
                cursor.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
            else:
                cursor.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
        else:
            if count <= 0:
                raise HTTPException(status_code=400, detail="Invalid request")
            cursor.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, count))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart")
async def retrieve_cart(request: Request):
    try:
        data = await request.json()
        cart_id = data["cart_id"]
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid request")
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Cart not found")
        
        cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
        items = [{"item_id": row[0], "count": row[1]} for row in cursor.fetchall()]
    finally:
        conn.close()
    return {"items": items}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)