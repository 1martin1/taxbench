import sqlite3
import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator, model_validator
from typing import List
import sys

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    raise HTTPException(status_code=400, detail="Invalid request")

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

class RetrieveCardRequest(BaseModel):
    phone_numbers: List[str]

    @field_validator('phone_numbers')
    def check_non_empty(cls, v):
        if not v:
            raise ValueError("Phone numbers list is empty")
        return v

    @model_validator(mode='after')
    def check_max_length(self):
        if len(self.phone_numbers) > 100:
            raise ValueError("Too many phone numbers")
        return self

@app.on_event("startup")
def create_tables():
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS card_associations (
                credit_card TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card, phone)
            )
        """)
        conn.commit()
        conn.close()
        # Set secure permissions on the database file
        os.chmod('db.sqlite3', 0o600)
    except Exception as e:
        print(f"Failed to initialize database: {e}")
        raise

@app.post("/associate_card", status_code=201)
def associate_card(data: AssociateCardRequest):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO card_associations (credit_card, phone) VALUES (?, ?)",
            (data.credit_card, data.phone)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Duplicate association")
    finally:
        conn.close()
    return {}

@app.post("/retrieve_cards")
def retrieve_cards(data: RetrieveCardRequest):
    phones = data.phone_numbers
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    placeholders = ','.join('?' * len(phones))
    query = f"""
        SELECT credit_card FROM card_associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    try:
        cursor.execute(query, phones + [len(phones)])
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")
    results = cursor.fetchall()
    card_numbers = [row[0] for row in results]
    conn.close()
    if not card_numbers:
        raise HTTPException(status_code=404, detail="No cards found")
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)