from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

class RetrieveCardsRequest(BaseModel):
    phone_numbers: list[str]

@app.on_event("startup")
def startup():
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS card_phone (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (credit_card, phone)
        )
    """)
    conn.commit()
    conn.close()

@app.post("/associate_card", status_code=201)
async def associate_card(data: AssociateCardRequest):
    credit_card = data.credit_card
    phone = data.phone
    try:
        conn = sqlite3.connect("db.sqlite3")
        conn.execute("INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)", (credit_card, phone))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Association already exists")
    finally:
        conn.close()
    return {}

@app.post("/retrieve_cards")
async def retrieve_cards(data: RetrieveCardsRequest):
    phone_numbers = data.phone_numbers
    if not phone_numbers:
        raise HTTPException(status_code=400, detail="Phone numbers list cannot be empty")
    
    unique_phones = list(set(phone_numbers))
    placeholders = ','.join('?' * len(unique_phones))
    query = f"""
        SELECT credit_card FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = {len(unique_phones)}
    """
    
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute(query, unique_phones)
        results = cursor.fetchall()
        card_numbers = [row[0] for row in results]
        
        if not card_numbers:
            raise HTTPException(status_code=404, detail="No cards found")
            
        return {"card_numbers": card_numbers}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)