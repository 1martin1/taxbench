import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List
import uvicorn

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str] = Field(..., min_length=1, max_length=100)

@app.on_event("startup")
async def startup():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_phone (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (credit_card, phone)
        )
    """)
    conn.commit()
    conn.close()

@app.post("/associate_card", status_code=201)
async def associate_card(request: AssociateCardRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)",
            (request.credit_card, request.phone)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return {"status": "created"}

@app.post("/retrieve_cards")
async def retrieve_card(request: RetrieveCardsRequest):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    phones = request.phone_numbers
    placeholders = ','.join('?' for _ in phones)
    query = f"""
        SELECT credit_card FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    try:
        cursor.execute(query, phones + [len(phones)])
        results = cursor.fetchall()
    finally:
        conn.close()
    
    card_numbers = [row[0] for row in results]
    if not card_numbers:
        raise HTTPException(status_code=404, detail="Not found")
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)