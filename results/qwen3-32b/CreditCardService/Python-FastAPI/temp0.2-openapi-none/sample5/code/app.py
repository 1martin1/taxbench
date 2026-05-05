from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, StringConstraints
from typing import Annotated, List
import sqlite3

class AssociateCardRequest(BaseModel):
    credit_card: Annotated[str, StringConstraints(min_length=1)]
    phone: Annotated[str, StringConstraints(min_length=1)]

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[Annotated[str, StringConstraints(min_length=1)]]

app = FastAPI()

def init_db():
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS card_phone_association (
                credit_card TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card, phone)
            )
        ''')
        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError("Failed to initialize database") from e
    finally:
        if 'conn' in locals() and conn:
            conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/associate_card", status_code=201)
async def associate_card(request: AssociateCardRequest):
    credit_card = request.credit_card
    phone = request.phone
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute('INSERT INTO card_phone_association (credit_card, phone) VALUES (?, ?)', (credit_card, phone))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid request")
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error") from e
    finally:
        if conn:
            conn.close()
    return Response(status_code=201)

@app.post("/retrieve_cards")
async def retrieve_cards(request: RetrieveCardsRequest):
    original_phone_numbers = request.phone_numbers
    unique_phone_numbers = list(set(original_phone_numbers))
    
    if not unique_phone_numbers:
        raise HTTPException(status_code=400, detail="Invalid request")
    
    conn = None
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        placeholders = ','.join('?' for _ in unique_phone_numbers)
        query = f'''
            SELECT credit_card FROM card_phone_association
            WHERE phone IN ({placeholders})
            GROUP BY credit_card
            HAVING COUNT(DISTINCT phone) = ?
        '''
        params = unique_phone_numbers + [len(unique_phone_numbers)]
        c.execute(query, params)
        results = c.fetchall()
        card_numbers = [row[0] for row in results]
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error") from e
    finally:
        if conn:
            conn.close()
    
    if not card_numbers:
        raise HTTPException(status_code=404, detail="Not found")
    
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)