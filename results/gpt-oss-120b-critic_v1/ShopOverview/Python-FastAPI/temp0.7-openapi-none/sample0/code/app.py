import os
import sqlite3
import csv
import io
from typing import Optional, List, Generator
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Depends, Request, Response, status, Cookie, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
from jose import JWTError, jwt
from jinja2 import Environment, select_autoescape
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for JWT signing")

# JWT settings
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# FastAPI app
app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# Database path
DB_PATH = "db.sqlite3"

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS wares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()

init_db()

# Pydantic models
class RegisterModel(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class LoginModel(BaseModel):
    email: EmailStr
    password: str

class UploadWaresModel(BaseModel):
    csv: str

# Utility functions
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

async def get_current_merchant(auth_cookie: Optional[str] = Cookie(None)):
    if not auth_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(auth_cookie)
    merchant_id = payload.get("sub")
    if merchant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, email, name FROM merchants WHERE id = ?", (merchant_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}

# Jinja environment with autoescaping enabled
jinja_env = Environment(autoescape=select_autoescape(enabled_extensions=('html', 'xml'), default=True))

# Endpoints
@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterModel):
    password_hash = get_password_hash(payload.password)
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
                (payload.email, payload.name, password_hash),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(payload: LoginModel, response: Response):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, password_hash FROM merchants WHERE email = ?", (payload.email,))
        row = cur.fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid email or password"},
        )
    access_token = create_access_token(data={"sub": row["id"]})
    # Secure cookie (HTTPS only). In development over HTTP, set secure=False if needed.
    response.set_cookie(
        key="AUTH_COOKIE",
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=True,  # ensures transmission only over HTTPS
    )
    return PlainTextResponse(content="Login successful", media_type="text/plain")

@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadWaresModel, merchant: dict = Depends(get_current_merchant)):
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=400, detail="CSV content is empty")
    csv_file = io.StringIO(csv_content)
    reader = csv.DictReader(csv_file)
    expected_fields = {"name", "description", "price"}
    if set(reader.fieldnames or []) != expected_fields:
        raise HTTPException(status_code=400, detail="CSV must contain exactly name, description, price columns")
    # Insert rows one by one to avoid large memory usage
    with get_db_connection() as conn:
        cur = conn.cursor()
        for line_num, row in enumerate(reader, start=2):
            try:
                name = row["name"].strip()
                description = row["description"].strip()
                price = float(row["price"])
            except (KeyError, ValueError) as e:
                raise HTTPException(status_code=400, detail=f"Invalid data at line {line_num}: {e}")
            if not name:
                raise HTTPException(status_code=400, detail=f"Missing name at line {line_num}")
            cur.execute(
                "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
                (merchant["id"], name, description, price),
            )
        conn.commit()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    request: Request,
    minPrice: Optional[float] = Query(None, description="Minimum price of wares"),
    maxPrice: Optional[float] = Query(None, description="Maximum price of wares"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of wares to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    merchant: dict = Depends(get_current_merchant),
):
    query = """
        SELECT w.name AS ware_name, w.description, w.price, m.email AS merchant_email
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
    query += " ORDER BY w.name ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Wares List</title>
        <style>
            table { border-collapse: collapse; width: 80%; margin: 20px auto; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h2 style="text-align:center;">Available Wares</h2>
        <table>
            <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Price</th>
                <th>Merchant Email</th>
            </tr>
            {% for row in rows %}
            <tr>
                <td>{{ row.ware_name }}</td>
                <td>{{ row.description }}</td>
                <td>{{ "%.2f"|format(row.price) }}</td>
                <td>{{ row.merchant_email }}</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    template = jinja_env.from_string(html_template)
    rendered = template.render(rows=rows)
    return HTMLResponse(content=rendered)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)