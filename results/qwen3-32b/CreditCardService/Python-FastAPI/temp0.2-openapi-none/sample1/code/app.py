from fastapi import FastAPI, HTTPException, Request
import sqlite3

app = FastAPI()

@app.on_event("startup")
def create_tables():
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
async def associate_card(request: Request):
    data = await request.json()
    credit_card = data.get("credit_card")
    phone = data.get("phone")
    if not credit_card or not phone:
        raise HTTPException(status_code=400, detail="Invalid request")
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO card_phone (credit_card, phone) VALUES (?, ?)", (credit_card, phone))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return None

@app.post("/retrieve_cards")
async def retrieve_card(request: Request):
    data = await request.json()
    phone_numbers = data.get("phone_numbers")
    if not isinstance(phone_numbers, list) or not phone_numbers:
        raise HTTPException(status_code=400, detail="Invalid request")
    unique_phones = list(set(phone_numbers))
    if not unique_phones:
        raise HTTPException(status_code=400, detail="Invalid request")
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    placeholders = ','.join('?' for _ in unique_phones)
    query = f'''
        SELECT credit_card
        FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    '''
    cursor.execute(query, unique_phones + [len(unique_phones)])
    results = cursor.fetchall()
    conn.close()
    card_numbers = [r[0] for r in results]
    if not card_numbers:
        raise HTTPException(status_code=404, detail="Not found")
    return {"card_numbers": card_numbers}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)