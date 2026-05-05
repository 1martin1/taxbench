import sqlite3
from fastapi import FastAPI, HTTPException, Body, Response
import uuid

app = FastAPI()

def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()
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

@app.on_event("startup")
def on_startup():
    create_tables()

@app.post("/create_cart", status_code=201)
async def create_cart():
    cart_id = str(uuid.uuid4())
    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    conn.commit()
    conn.close()
    return {"cart_id": cart_id}

@app.post("/add_to_cart")
async def add_to_cart(payload: dict = Body(...)):
    cart_id = payload["cart_id"]
    item_id = payload["item_id"]
    count = payload["count"]

    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Check if item exists in cart
    cursor.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
    result = cursor.fetchone()

    if result:
        existing_count = result[0]
        new_count = existing_count + count
        if new_count < 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        if new_count == 0:
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

@app.post("/retrieve_cart")
async def retrieve_cart(payload: dict = Body(...)):
    cart_id = payload["cart_id"]

    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    # Check if cart exists
    cursor.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Retrieve items
    cursor.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
    items = cursor.fetchall()
    conn.close()

    item_list = [{"item_id": item[0], "count": item[1]} for item in items]
    return {"items": item_list}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)