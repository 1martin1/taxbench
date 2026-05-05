import re
import sqlite3
from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator, model_validator
from typing import List

app = FastAPI()

@app.on_event("startup")
def create_tables():
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS card_phone (
                credit_card TEXT NOT NULL,
                phone TEXT NOT NULL,
                PRIMARY KEY (credit_card, phone)
            )
        """)
        conn.commit()

class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

    @field_validator('credit_card')
    def validate_credit_card(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Credit card cannot be empty")
        if not re.fullmatch(r'^\d{13,19}$', v):
            raise ValueError("Invalid credit card format")
        return v

    @field_validator('phone')
    def validate_phone(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Phone cannot be empty")
        if not re.fullmatch(r'^\d{7,15}$', v):
            raise ValueError("Invalid phone format")
        return v

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str]

    @model_validator(mode='after')
    def check_phone_numbers(self):
        if len(self.phone_numbers) > 100:
            raise ValueError("Too many phone numbers (max 100 allowed)")
        stripped_numbers = [num.strip() for num in self.phone_numbers]
        if not stripped_numbers or any(not n for n in stripped_numbers):
            raise ValueError("Phone number cannot be empty")
        if any(not re.fullmatch(r'^\d{7,15}$', n) for n in stripped_numbers):
            raise ValueError("Invalid phone number format")
        self.phone_numbers = stripped_numbers
        return self

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    raise HTTPException(status_code=400, detail="Invalid request")

@app.post("/associate_card", status_code=201)
async def associate_card(request: AssociateCardRequest):
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)",
                (request.credit_card, request.phone)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Invalid request")
    return {"detail": "Association created successfully"}

@app.post("/retrieve_cards")
async def retrieve_cards(request: RetrieveCardsRequest):
    phone_numbers = request.phone_numbers
    
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        placeholders = ", ".join("?" * len(phone_numbers))
        query = f"""
            SELECT credit_card
            FROM card_phone
            WHERE phone IN ({placeholders})
            GROUP BY credit_card
            HAVING COUNT(DISTINCT phone) = ?
        """
        params = phone_numbers + [len(phone_numbers)]
        cursor.execute(query, params)
        results = cursor.fetchall()
    
    card_numbers = [row[0] for row in results]
    
    if not card_numbers:
        raise HTTPException(status_code=404, detail="Not found")
    
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)