from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import sqlite3

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str]

class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str]

@app.on_event("startup")
def startup():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_phone_associations (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            UNIQUE(credit_card, phone)
        )
    """)
    conn.commit()
    conn.close()

@app.post("/associate_card", status_code=201)
def associate_card(request: AssociateCardRequest):
    credit_card = request.credit_card
    phone = request.phone
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO card_phone_associations (credit_card, phone) VALUES (?, ?)", (credit_card, phone))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return {}

@app.post("/retrieve_cards")
def retrieve_cards(request: RetrieveCardsRequest):
    phone_numbers = request.phone_numbers
    if not phone_numbers:
        raise HTTPException(status_code=400, detail="Invalid request")
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(phone_numbers))
        query = f"""
            SELECT credit_card FROM card_phone_associations
            WHERE phone IN ({placeholders})
            GROUP BY credit_card
            HAVING COUNT(DISTINCT phone) = ?
        """
        params = phone_numbers + [len(phone_numbers)]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        card_numbers = [row[0] for row in rows]
        if not card_numbers:
            raise HTTPException(status_code=404, detail="Not found")
        return RetrieveCardsResponse(card_numbers=card_numbers)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)