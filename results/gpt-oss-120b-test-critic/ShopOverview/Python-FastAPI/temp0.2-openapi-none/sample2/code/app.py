import os
import sqlite3
import csv
import io
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Depends, Request, Response, status, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import JWTError, jwt
from email_validator import validate_email, EmailNotValidError
from typing import Optional, List

# Initialize FastAPI app
app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# Database file
DB_PATH = "db.sqlite3"

# ---------- Database utilities ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # merchants table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    # wares table
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

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Pydantic models ----------
class RegisterModel(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class LoginModel(BaseModel):
    email: EmailStr
    password: str

class UploadWaresModel(BaseModel):
    csv: str

# ---------- Auth utilities ----------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

async def get_current_merchant(request: Request):
    token = request.cookies.get("AUTH_COOKIE")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    merchant_id = payload.get("sub")
    if merchant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    # fetch merchant from DB
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return dict(row)

# ---------- Endpoints ----------
@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterModel):
    # Validate email (email_validator provides extra checks)
    try:
        validate_email(payload.email)
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))

    password_hash = get_password_hash(payload.password)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.name, password_hash)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    conn.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(payload: LoginModel, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE email = ?", (payload.email,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # Create JWT token
    token_data = {"sub": row["id"], "email": row["email"]}
    token = create_access_token(token_data)
    # Set cookie
    response.set_cookie(
        key="AUTH_COOKIE",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return {"message": "Login successful"}

@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadWaresModel, merchant: dict = Depends(get_current_merchant)):
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=400, detail="CSV content is empty")
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f)
    required_fields = {"name", "description", "price"}
    if not required_fields.issubset(reader.fieldnames or []):
        raise HTTPException(status_code=400, detail="CSV must contain name, description, price columns")
    rows_to_insert = []
    for idx, row in enumerate(reader, start=2):  # start=2 to account for header line
        name = row.get("name", "").strip()
        description = row.get("description", "").strip()
        price_str = row.get("price", "").strip()
        if not name or not price_str:
            raise HTTPException(status_code=400, detail=f"Missing name or price at line {idx}")
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid price at line {idx}")
        rows_to_insert.append((merchant["id"], name, description, price))
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
def list_wares(
    request: Request,
    minPrice: Optional[float] = Query(None, alias="minPrice"),
    maxPrice: Optional[float] = Query(None, alias="maxPrice"),
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
    query += " ORDER BY w.price ASC"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    conn.close()

    # Simple HTML template
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Wares List</title>
        <style>
            table {{ border-collapse: collapse; width: 80%; margin: auto; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            caption {{ font-size: 1.5em; margin: 10px; }}
        </style>
    </head>
    <body>
        <table>
            <caption>Available Wares</caption>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Description</th>
                    <th>Price</th>
                    <th>Merchant Email</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </body>
    </html>
    """
    row_html = ""
    for r in rows:
        row_html += f"""
        <tr>
            <td>{r["ware_name"]}</td>
            <td>{r["description"]}</td>
            <td>{r["price"]:.2f}</td>
            <td>{r["merchant_email"]}</td>
        </tr>
        """
    final_html = html_template.format(rows=row_html or "<tr><td colspan='4'>No wares found.</td></tr>")
    return HTMLResponse(content=final_html)

# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)