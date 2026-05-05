from fastapi import FastAPI, HTTPException, Body
import sqlite3

app = FastAPI()

@app.on_event("startup")
def startup():
    app.db = sqlite3.connect('db.sqlite3', check_same_thread=False)
    app.db.execute("""
        CREATE TABLE IF NOT EXISTS card_phone (
            credit_card TEXT NOT NULL,
            phone TEXT NOT NULL,
            PRIMARY KEY (credit_card, phone)
        )
    """)
    app.db.commit()

@app.on_event("shutdown")
def shutdown():
    app.db.close()

@app.post("/associate_card", status_code=201)
def associate_card(
    credit_card: str = Body(..., description="Number of the credit card"),
    phone: str = Body(..., description="Phone number")
):
    try:
        app.db.execute(
            "INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)",
            (credit_card, phone)
        )
        app.db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Association already exists")
    return

@app.post("/retrieve_cards")
def retrieve_cards(
    phone_numbers: list[str] = Body(..., description="Phone numbers", alias="phone_numbers")
):
    unique_phones = list(set(phone_numbers))
    if not unique_phones:
        raise HTTPException(status_code=400, detail="Invalid request")
    
    placeholders = ','.join('?' * len(unique_phones))
    query = f"""
        SELECT credit_card FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    
    cursor = app.db.cursor()
    cursor.execute(query, unique_phones + [len(unique_phones)])
    results = cursor.fetchall()
    card_numbers = [row[0] for row in results]
    
    if not card_numbers:
        raise HTTPException(status_code=404, detail="Not found")
    
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)