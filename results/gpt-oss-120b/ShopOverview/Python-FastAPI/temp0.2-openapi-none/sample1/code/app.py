import os
import sqlite3
import csv
from io import StringIO
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Response, status
from fastapi.security import APIKeyCookie
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import JWTError, jwt

# ---------- Configuration ----------
DATABASE_PATH = "db.sqlite3"
SECRET_KEY = os.getenv("APP_SECRET", "default_secret_key")
ALGORITHM = "HS256"

# ---------- Initialize FastAPI ----------
app = FastAPI()

# ---------- Database ----------
conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def init_db():
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


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Security ----------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
api_key_cookie = APIKeyCookie(name="AUTH_COOKIE", auto_error=False)


def create_access_token(data: dict) -> str:
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def get_current_merchant(token: Optional[str] = Depends(api_key_cookie)):
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        merchant_id: int = payload.get("sub")
        if merchant_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    cur = conn.cursor()
    cur.execute("SELECT id, email, name FROM merchants WHERE id = ?", (merchant_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


# ---------- Pydantic Models ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UploadWaresRequest(BaseModel):
    csv: str


# ---------- Endpoints ----------
@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
def register_merchant(req: RegisterRequest):
    cur = conn.cursor()
    cur.execute("SELECT id FROM merchants WHERE email = ?", (req.email,))
    if cur.fetchone():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    password_hash = pwd_context.hash(req.password)
    cur.execute(
        "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
        (req.email, req.name, password_hash),
    )
    conn.commit()
    return {"message": "Merchant registered successfully"}


@app.post("/merchants/login")
def login_merchant(req: LoginRequest, response: Response):
    cur = conn.cursor()
    cur.execute("SELECT id, password_hash FROM merchants WHERE email = ?", (req.email,))
    row = cur.fetchone()
    if not row or not pwd_context.verify(req.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_access_token({"sub": row["id"]})
    response.set_cookie(key="AUTH_COOKIE", value=token, httponly=True, samesite="lax")
    return "Login successful"


@app.post("/merchants/upload-wares")
def upload_wares(req: UploadWaresRequest, merchant: dict = Depends(get_current_merchant)):
    csv_content = req.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content is empty")

    f = StringIO(csv_content)
    reader = csv.DictReader(f)
    expected_fields = {"name", "description", "price"}
    if not reader.fieldnames or set(reader.fieldnames) != expected_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV must contain exactly columns: name, description, price",
        )

    cur = conn.cursor()
    for line_num, row in enumerate(reader, start=2):
        try:
            price = float(row["price"])
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid price on line {line_num}: {row['price']}",
            )
        cur.execute(
            """
            INSERT INTO wares (merchant_id, name, description, price)
            VALUES (?, ?, ?, ?)
            """,
            (merchant["id"], row["name"], row["description"], price),
        )
    conn.commit()
    return {"message": "Wares uploaded successfully"}


@app.get("/wares")
def list_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    merchant: dict = Depends(get_current_merchant),
):
    query = """
        SELECT w.name, w.description, w.price, m.email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
    """
    conditions = []
    params = []

    if minPrice is not None:
        conditions.append("w.price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        conditions.append("w.price <= ?")
        params.append(maxPrice)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY w.price"

    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()

    html = """<html><head><title>Wares</title></head><body>"""
    html += "<h1>Available Wares</h1>"
    html += "<table border='1' cellpadding='5' cellspacing='0'>"
    html += "<tr><th>Name</th><th>Description</th><th>Price</th><th>Merchant Email</th></tr>"
    for r in rows:
        html += f"<tr><td>{r['name']}</td><td>{r['description']}</td><td>{r['price']}</td><td>{r['email']}</td></tr>"
    html += "</table></body></html>"

    return HTMLResponse(content=html, status_code=200)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)