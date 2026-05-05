from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import uuid

app = FastAPI()

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
    items: list[CartItem]

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS carts (cart_id TEXT PRIMARY KEY)")
    c.execute("""CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT,
                item_id INTEGER,
                count INTEGER,
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE)""")
    conn.commit()
    conn.close()

@app.post("/create_cart", response_model=CreateCartResponse, status_code=201)
async def create_cart():
    while True:
        cart_id = str(uuid.uuid4())
        conn = sqlite3.connect('db.sqlite3')
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()
        success = False
        try:
            c.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
            conn.commit()
            success = True
        except sqlite3.IntegrityError:
            success = False
        finally:
            conn.close()
        if success:
            return {"cart_id": cart_id}

@app.post("/add_to_cart")
async def add_to_cart(request: AddToCartRequest):
    cart_id = request.cart_id
    item_id = request.item_id
    count = request.count

    if count == 0:
        raise HTTPException(status_code=400, detail="Invalid request")

    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # Check if cart exists
    c.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Check if item exists in cart_items
    c.execute("SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
    result = c.fetchone()

    if result:
        current_count = result[0]
        new_count = current_count + count
        if new_count <= 0:
            c.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
        else:
            c.execute("UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?", (new_count, cart_id, item_id))
    else:
        if count <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Invalid request")
        else:
            c.execute("INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)", (cart_id, item_id, count))

    conn.commit()
    conn.close()
    return {"message": "Item added or removed successfully"}

@app.post("/retrieve_cart", response_model=RetrieveCartResponse)
async def retrieve_cart(request: RetrieveCartRequest):
    cart_id = request.cart_id
    conn = sqlite3.connect('db.sqlite3')
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # Check if cart exists
    c.execute("SELECT 1 FROM carts WHERE cart_id = ?", (cart_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Cart not found")

    # Get items
    c.execute("SELECT item_id, count FROM cart_items WHERE cart_id = ?", (cart_id,))
    items = [{"item_id": row[0], "count": row[1]} for row in c.fetchall()]
    conn.close()
    return {"items": items}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)