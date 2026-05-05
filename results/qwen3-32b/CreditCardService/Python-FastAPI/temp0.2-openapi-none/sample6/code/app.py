from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import List
import sqlite3
import uvicorn

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str]

def get_db_connection():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def startup():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS card_phone (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (credit_card, phone)
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/associate_card", status_code=201)
def associate_card(request: AssociateCardRequest):
    credit_card = request.credit_card
    phone = request.phone

    conn = get_db_connection()
    existing = conn.execute('''
        SELECT * FROM card_phone 
        WHERE credit_card = ? AND phone = ?
    ''', (credit_card, phone)).fetchone()
    
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Association already exists")
    
    conn.execute('''
        INSERT INTO card_phone (credit_card, phone) 
        VALUES (?, ?)
    ''', (credit_card, phone))
    conn.commit()
    conn.close()
    return {"status": "created"}

@app.post("/retrieve_cards")
def retrieve_card(request: RetrieveCardsRequest):
    phone_numbers = request.phone_numbers
    if not phone_numbers:
        raise HTTPException(status_code=400, detail="Phone numbers array is empty")
    
    conn = get_db_connection()
    placeholders = ','.join('?' for _ in phone_numbers)
    query = f'''
        SELECT credit_card FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    '''
    params = phone_numbers + [len(phone_numbers)]
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    card_numbers = [row['credit_card'] for row in rows]
    if not card_numbers:
        raise HTTPException(status_code=404, detail="No cards found")
    
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)