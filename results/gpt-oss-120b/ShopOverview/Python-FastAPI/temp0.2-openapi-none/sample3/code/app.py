import os
import sqlite3
import csv
from io import StringIO
from typing import Optional, List

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")
DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24  # 1 day

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# -------------------- Database --------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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
    conn.close()

init_db()

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

# -------------------- Utility Functions --------------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def get_current_merchant(token: Optional[str] = Cookie(None)):
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(token)
    merchant_id = payload.get("sub")
    if merchant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return dict(row)

# -------------------- FastAPI App --------------------
app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# -------------------- Endpoints --------------------
@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
def register_merchant(req: RegisterRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (req.email, req.name, hash_password(req.password))
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    conn.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(req: LoginRequest, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE email = ?", (req.email,))
    row = cur.fetchone()
    conn.close()
    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    token = create_access_token({"sub": row["id"]})
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=JWT_EXPIRE_MINUTES * 60,
        path="/"
    )
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
def upload_wares(req: UploadWaresRequest, merchant: dict = Depends(get_current_merchant)):
    csv_content = req.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content is empty")
    f = StringIO(csv_content)
    reader = csv.reader(f)
    rows_to_insert = []
    line_num = 0
    for row in reader:
        line_num += 1
        if len(row) != 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid CSV format at line {line_num}: expected 3 columns"
            )
        name, description, price_str = row
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid price at line {line_num}"
            )
        rows_to_insert.append((merchant["id"], name.strip(), description.strip(), price))

    conn = get_db()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
        rows_to_insert
    )
    conn.commit()
    conn.close()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
def get_wares(
    request: Request,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    merchant: dict = Depends(get_current_merchant)
):
    query = """
        SELECT w.name as ware_name, w.description, w.price, m.email as merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
        WHERE 1=1
    """
    params: List = []
    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    conn.close()

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
            <td>{row["ware_name"]}</td>
            <td>{row["description"]}</td>
            <td>{row["price"]}</td>
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
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)