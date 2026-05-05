from fastapi import FastAPI, HTTPException, status, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List
import sqlite3

app = FastAPI()

class AssociateCardRequest(BaseModel):
    credit_card: str = Field(..., max_length=255)
    phone: str = Field(..., max_length=255)

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str] = Field(..., max_length=100)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS associations (
            credit_card TEXT,
            phone TEXT
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(request: AssociateCardRequest):
    credit_card = request.credit_card
    phone = request.phone
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO associations (credit_card, phone) VALUES (?, ?)", (credit_card, phone))
        conn.commit()
    finally:
        conn.close()
    return {}

@app.post("/retrieve_cards")
def retrieve_card(request: RetrieveCardsRequest):
    phone_numbers = request.phone_numbers
    if not phone_numbers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request")
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        placeholders = ','.join('?' * len(phone_numbers))
        query = f"""
            SELECT credit_card FROM associations
            WHERE phone IN ({placeholders})
            GROUP BY credit_card
            HAVING COUNT(DISTINCT phone) = ?
        """
        parameters = phone_numbers + [len(phone_numbers)]
        cursor.execute(query, parameters)
        results = cursor.fetchall()
        card_numbers = [row[0] for row in results]
        if not card_numbers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return {"card_numbers": card_numbers}
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)