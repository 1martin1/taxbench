from fastapi import FastAPI, HTTPException, Response
import sqlite3
import uuid
from datetime import datetime, timezone

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/click", status_code=201)
async def create_click():
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        click_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return Response(status_code=201)

@app.get("/clicks")
async def get_clicks(date: str, direction: str):
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        date_naive = datetime.combine(date_obj, datetime.min.time())
        date_aware = date_naive.replace(tzinfo=timezone.utc)
        date_iso = date_aware.isoformat()
        if direction not in ["<", ">", "<=", ">="]:
            raise HTTPException(status_code=400, detail="Invalid direction")
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?"
        cursor.execute(query, (date_iso,))
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="No clicks found")
        clicks = [{"id": row[0], "timestamp": row[1]} for row in rows]
        return clicks
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)