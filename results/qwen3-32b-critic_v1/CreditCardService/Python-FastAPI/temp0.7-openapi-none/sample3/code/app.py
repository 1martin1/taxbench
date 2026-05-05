from fastapi import FastAPI, HTTPException, Body
import sqlite3
from pydantic import BaseModel, StringConstraints, Field
from typing import Annotated, List

app = FastAPI()

# Configuration
MAX_PHONE_NUMBERS = 100

# Input validation models
class AssociateCardRequest(BaseModel):
    credit_card: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r'^[\d\s\-]+$')]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^\+[1-9]\d{1,14}$')]

class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^\+[1-9]\d{1,14}$')]]

class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str]

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect('db.sqlite3')
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

@app.post("/associate_card", status_code=201)
async def associate_card(data: AssociateCardRequest):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)',
            (data.credit_card, data.phone)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Association already exists")
    finally:
        conn.close()
    return {}

@app.post("/retrieve_cards")
async def retrieve_card(data: RetrieveCardsRequest):
    phone_numbers = data.phone_numbers
    
    if not phone_numbers:
        raise HTTPException(status_code=400, detail="No phone numbers provided")
    
    if len(phone_numbers) > MAX_PHONE_NUMBERS:
        raise HTTPException(status_code=400, detail="Too many phone numbers requested")
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        # Create temporary table for query optimization
        cursor.execute("CREATE TEMPORARY TABLE IF NOT EXISTS temp_phones (phone TEXT)")
        
        # Insert phone numbers into temporary table
        cursor.executemany(
            "INSERT INTO temp_phones (phone) VALUES (?)", 
            [(phone,) for phone in phone_numbers]
        )
        
        # Query using JOIN instead of IN clause
        query = '''
            SELECT credit_card 
            FROM card_phone 
            JOIN temp_phones ON card_phone.phone = temp_phones.phone
            GROUP BY credit_card
            HAVING COUNT(DISTINCT phone) = ?
        '''
        cursor.execute(query, (len(phone_numbers),))
        rows = cursor.fetchall()
        
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    
    card_numbers = [row[0] for row in rows]
    if not card_numbers:
        raise HTTPException(status_code=404, detail="No cards found")
    
    return RetrieveCardsResponse(card_numbers=card_numbers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)