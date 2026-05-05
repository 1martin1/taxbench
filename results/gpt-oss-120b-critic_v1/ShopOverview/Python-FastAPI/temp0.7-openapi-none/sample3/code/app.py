import os
import csv
import html
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    Request,
    Response,
    Cookie,
    Header,
)
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# -------------------------------------------------
# Configuration
# -------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("Environment variable APP_SECRET must be set")

DATABASE = "db.sqlite3"
COOKIE_NAME = "AUTH_COOKIE"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day
MAX_CSV_SIZE = 1_000_000  # 1 MB
MAX_WARES_RETURNED = 1000
RATE_LIMIT = 100  # requests
RATE_PERIOD = 60  # seconds

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# -------------------------------------------------
# Simple in‑memory rate limiting middleware
# -------------------------------------------------
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.ip_records: Dict[str, List[float]] = {}

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        now = datetime.utcnow().timestamp()
        timestamps = self.ip_records.get(client_ip, [])
        # Remove timestamps older than RATE_PERIOD
        timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
        timestamps.append(now)
        self.ip_records[client_ip] = timestamps
        if len(timestamps) > RATE_LIMIT:
            return JSONResponse(
                {"error": "Too many requests, please try again later."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        response = await call_next(request)
        return response

app.add_middleware(RateLimitMiddleware)

# -------------------------------------------------
# Database utilities
# -------------------------------------------------
def get_db():
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

# -------------------------------------------------
# Pydantic models
# -------------------------------------------------
class RegisterModel(BaseModel):
    email: EmailStr
    name: str
    password: str

class LoginModel(BaseModel):
    email: EmailStr
    password: str

class UploadCSVModel(BaseModel):
    csv: str

# -------------------------------------------------
# Auth utilities
# -------------------------------------------------
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

def get_current_merchant(auth_cookie: Optional[str] = Cookie(None)):
    if not auth_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(auth_cookie)
    merchant_id = payload.get("sub")
    email = payload.get("email")
    if merchant_id is None or email is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, email, name FROM merchants WHERE id = ?", (merchant_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}

# -------------------------------------------------
# Exception handlers to match OpenAPI schema
# -------------------------------------------------
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        # Return object with 'error' field as defined in the spec
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    # Default handling
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
def register_merchant(payload: RegisterModel):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM merchants WHERE email = ?", (payload.email,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    password_hash = get_password_hash(payload.password)
    cur.execute(
        "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
        (payload.email, payload.name, password_hash),
    )
    conn.commit()
    conn.close()
    return JSONResponse(content={"message": "Merchant registered successfully"}, status_code=status.HTTP_201_CREATED)

@app.post("/merchants/login")
def login_merchant(payload: LoginModel, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, password_hash FROM merchants WHERE email = ?", (payload.email,))
    row = cur.fetchone()
    conn.close()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token_data = {"sub": row["id"], "email": payload.email}
    access_token = create_access_token(token_data)
    # Set HttpOnly, Secure and SameSite cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=True,
        samesite="lax",
    )
    # Returning a plain string fulfills the OpenAPI schema (type: string)
    return PlainTextResponse(content="Login successful", media_type="application/json")

@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadCSVModel, merchant: dict = Depends(get_current_merchant)):
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content is empty")
    if len(csv_content.encode("utf-8")) > MAX_CSV_SIZE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content exceeds maximum allowed size")
    # Use csv.DictReader on a list of lines to avoid loading huge files into memory beyond splitlines
    lines = csv_content.splitlines()
    reader = csv.DictReader(lines)
    expected_fields = {"name", "description", "price"}
    if set(reader.fieldnames or []) != expected_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV must contain exactly columns: name, description, price",
        )
    rows_to_insert = []
    for idx, row in enumerate(reader, start=1):
        name = row.get("name", "").strip()
        description = row.get("description", "").strip()
        price_str = row.get("price", "").strip()
        if not name or not price_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Row {idx}: 'name' and 'price' are required",
            )
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Row {idx}: price must be a number",
            )
        rows_to_insert.append((merchant["id"], name, description, price))
    if not rows_to_insert:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid rows found in CSV")
    conn = get_db()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
        rows_to_insert,
    )
    conn.commit()
    conn.close()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    merchant: dict = Depends(get_current_merchant),
):
    query = """
        SELECT w.name as ware_name, w.price, w.description, m.email as merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
        WHERE 1=1
    """
    params: List[Any] = []
    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)
    query += f" LIMIT {MAX_WARES_RETURNED}"
    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    conn.close()
    # Build escaped HTML table
    html_parts = [
        """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Wares List</title>
            <style>
                table {border-collapse: collapse; width: 80%; margin: 20px auto;}
                th, td {border: 1px solid #ddd; padding: 8px; text-align: left;}
                th {background-color: #f2f2f2;}
            </style>
        </head>
        <body>
            <h2 style="text-align:center;">Available Wares</h2>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Price</th>
                    <th>Description</th>
                    <th>Merchant Email</th>
                </tr>
        """
    ]
    for row in rows:
        name = html.escape(row["ware_name"] or "")
        price = f"{row['price']:.2f}"
        description = html.escape(row["description"] or "")
        merchant_email = html.escape(row["merchant_email"] or "")
        html_parts.append(
            f"""
                <tr>
                    <td>{name}</td>
                    <td>{price}</td>
                    <td>{description}</td>
                    <td>{merchant_email}</td>
                </tr>
            """
        )
    html_parts.append(
        """
            </table>
        </body>
        </html>
        """
    )
    full_html = "".join(html_parts)
    return HTMLResponse(content=full_html)

# -------------------------------------------------
# Run the application
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)