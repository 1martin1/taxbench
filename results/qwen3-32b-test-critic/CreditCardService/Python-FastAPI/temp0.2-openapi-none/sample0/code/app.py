from fastapi import FastAPI, HTTPException, status
import sqlite3
from pydantic import BaseModel
from typing import List

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

class RetrieveCardRequest(BaseModel):
    phone_numbers: List[str]

def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_phone_association (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (credit_card, phone)
        )
    """)
    conn.commit()
    conn.close()

async def lifespan(app: FastAPI):
    create_tables()
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(request: AssociateCardRequest):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO card_phone_association (credit_card, phone) VALUES (?, ?)", 
                       (request.credit_card, request.phone))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Association already exists")
    finally:
        conn.close()
    return {}

@app.post("/retrieve_cards")
def retrieve_card(request: RetrieveCardRequest):
    phone_numbers = request.phone_numbers
    if not phone_numbers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phone numbers list cannot be empty")
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    placeholders = ','.join('?' for _ in phone_numbers)
    query = f"""
    SELECT credit_card FROM card_phone_association
    WHERE phone IN ({placeholders})
    GROUP BY credit_card
    HAVING COUNT(DISTINCT phone) = {len(phone_numbers)}
    """
    
    try:
        cursor.execute(query, phone_numbers)
        results = cursor.fetchall()
        card_numbers = [row[0] for row in results]
        
        if not card_numbers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No cards found")
            
        return {"card_numbers": card_numbers}
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)