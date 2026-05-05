import os
import sqlite3
import csv
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Cookie
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import JWTError, jwt
from starlette.middleware.sessions import SessionMiddleware

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")
AUTH_COOKIE_NAME = "AUTH_COOKIE"
ALGORITHM = "HS256"

# -------------------- Database Setup --------------------
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS merchants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL
)
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS wares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    price REAL NOT NULL,
    FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
)
""")
conn.commit()

# -------------------- Security --------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict) -> str:
    return jwt.encode(data, APP_SECRET, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

def get_current_merchant(auth_cookie: Optional[str] = Cookie(default=None)):
    if not auth_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(auth_cookie)
    merchant_id = payload.get("merchant_id")
    if merchant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,))
    merchant = cur.fetchone()
    if merchant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return merchant

# -------------------- Pydantic Models --------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

# -------------------- FastAPI App --------------------
app = FastAPI(title="Merchant WebApp API", version="1.0.0")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)

# -------------------- Endpoints --------------------
@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
def register_merchant(payload: RegisterRequest):
    cur = conn.cursor()
    # Check if email already exists
    cur.execute("SELECT id FROM merchants WHERE email = ?", (payload.email,))
    if cur.fetchone():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    password_hash = get_password_hash(payload.password)
    try:
        cur.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.name, password_hash)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid data")
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(payload: LoginRequest, response: Response):
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE email = ?", (payload.email,))
    merchant = cur.fetchone()
    if not merchant or not verify_password(payload.password, merchant["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    token_data = {"merchant_id": merchant["id"], "email": merchant["email"]}
    token = create_access_token(token_data)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        path="/",
        max_age=60 * 60 * 24  # 1 day
    )
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadWaresRequest, merchant=Depends(get_current_merchant)):
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content is empty")
    reader = csv.reader(csv_content.splitlines())
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV has no rows")
    # Expect header? The description says CSV string with columns: name, description, price
    # We'll allow optional header detection: if first row contains non-numeric price, treat as header.
    start_idx = 0
    if len(rows[0]) == 3 and rows[0][2].lower() == "price":
        start_idx = 1
    wares_to_insert = []
    for i, row in enumerate(rows[start_idx:], start=start_idx+1):
        if len(row) != 3:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Row {i} does not have exactly 3 columns")
        name, description, price_str = row
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Invalid price on row {i}")
        wares_to_insert.append((merchant["id"], name.strip(), description.strip(), price))
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
        wares_to_insert
    )
    conn.commit()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
def list_wares(minPrice: Optional[float] = None, maxPrice: Optional[float] = None,
               merchant=Depends(get_current_merchant)):
    query = """
    SELECT w.name, w.description, w.price, m.email as merchant_email
    FROM wares w
    JOIN merchants m ON w.merchant_id = m.id
    """
    conditions: List[str] = []
    params: List = []
    if minPrice is not None:
        conditions.append("w.price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        conditions.append("w.price <= ?")
        params.append(maxPrice)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY w.price ASC"
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    # Build HTML table
    html = """
    <html>
    <head><title>Wares</title></head>
    <body>
    <h1>Available Wares</h1>
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Name</th>
            <th>Description</th>
            <th>Price</th>
            <th>Merchant Email</th>
        </tr>
    """
    for row in rows:
        html += f"""
        <tr>
            <td>{row["name"]}</td>
            <td>{row["description"]}</td>
            <td>{row["price"]:.2f}</td>
            <td>{row["merchant_email"]}</td>
        </tr>
        """
    html += """
    </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)