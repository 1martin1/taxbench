from fastapi import FastAPI, HTTPException
import sqlite3
from pydantic import BaseModel, field_validator, model_validator
from typing import List

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

    @field_validator('phone')
    def phone_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Phone number cannot be empty')
        return v

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str]

    @model_validator(mode='after')
    def validate_phone_numbers(self) -> 'RetrieveCardsRequest':
        for phone in self.phone_numbers:
            if not phone.strip():
                raise ValueError(f'Phone number {phone} cannot be empty')
        return self

def startup():
    conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS card_phone (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (credit_card, phone)
        )
    ''')
    conn.commit()
    conn.close()

app.add_event_handler("startup", startup)

@app.post("/associate_card", status_code=201)
def associate_card(request: AssociateCardRequest):
    try:
        conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)", (request.credit_card, request.phone))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Duplicate association")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return {"detail": "Association created successfully"}

@app.post("/retrieve_cards")
def retrieve_cards(request: RetrieveCardsRequest):
    phone_numbers = request.phone_numbers
    unique_phones = list(set(phone_numbers))
    if not unique_phones:
        return {"card_numbers": []}
    placeholders = ','.join('?' for _ in unique_phones)
    query = f"""
        SELECT credit_card FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    try:
        conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
        cursor = conn.cursor()
        params = unique_phones + [len(unique_phones)]
        cursor.execute(query, params)
        results = cursor.fetchall()
        card_numbers = [row[0] for row in results]
        if not card_numbers:
            raise HTTPException(status_code=404, detail="Not found")
        return {"card_numbers": card_numbers}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)