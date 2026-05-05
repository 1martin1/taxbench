import os
import csv
import sqlite3
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Request, Response, status, Body, Query
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import JWTError, jwt
from email_validator import validate_email, EmailNotValidError

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET", "defaultsecret")
ALGORITHM = "HS256"
COOKIE_NAME = "AUTH_COOKIE"

# -------------------- Database Setup --------------------
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def init_db():
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    cursor.execute("""
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

init_db()

# -------------------- Security --------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    return jwt.encode(data, APP_SECRET, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    return jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])

# -------------------- Pydantic Models --------------------
class RegisterModel(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class LoginModel(BaseModel):
    email: EmailStr
    password: str

class UploadWaresModel(BaseModel):
    csv: str

# -------------------- FastAPI App --------------------
app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# -------------------- Dependency --------------------
def get_current_merchant(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(token)
        merchant_id: int = payload.get("sub")
        if merchant_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return dict(row)

# -------------------- Endpoints --------------------
@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
def register_merchant(payload: RegisterModel):
    # Validate email format (already done by pydantic)
    try:
        # Extra validation using email_validator for stricter checks
        validate_email(payload.email)
    except EmailNotValidError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    password_hash = hash_password(payload.password)
    try:
        cursor.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.name, password_hash)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    return JSONResponse(content={"message": "Merchant registered successfully"}, status_code=status.HTTP_201_CREATED)

@app.post("/merchants/login")
def login_merchant(payload: LoginModel, response: Response):
    cursor.execute("SELECT * FROM merchants WHERE email = ?", (payload.email,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"error": "Invalid email or password"})
    merchant = dict(row)
    if not verify_password(payload.password, merchant["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"error": "Invalid email or password"})
    token_data = {"sub": merchant["id"]}
    token = create_access_token(token_data)
    response.set_cookie(key=COOKIE_NAME, value=token, httponly=True, path="/")
    return JSONResponse(content="Login successful", status_code=status.HTTP_200_OK)

@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadWaresModel, merchant: dict = Depends(get_current_merchant)):
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content is empty")
    reader = csv.reader(csv_content.splitlines())
    rows_to_insert = []
    line_num = 0
    for row in reader:
        line_num += 1
        if len(row) != 3:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Invalid CSV format at line {line_num}: expected 3 columns")
        name, description, price_str = row
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Invalid price at line {line_num}")
        rows_to_insert.append((merchant["id"], name.strip(), description.strip(), price))
    try:
        cursor.executemany(
            "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
            rows_to_insert
        )
        conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return JSONResponse(content={"message": "Wares uploaded successfully"}, status_code=status.HTTP_200_OK)

@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    request: Request,
    minPrice: Optional[float] = Query(None, description="Minimum price of wares"),
    maxPrice: Optional[float] = Query(None, description="Maximum price of wares"),
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
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    # Build HTML table
    html = """
    <html>
    <head><title>Wares List</title></head>
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
            <td>{row["price"]:.2f}</td>
            <td>{row["merchant_email"]}</td>
        </tr>
        """
    html += """
    </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=status.HTTP_200_OK)

# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)