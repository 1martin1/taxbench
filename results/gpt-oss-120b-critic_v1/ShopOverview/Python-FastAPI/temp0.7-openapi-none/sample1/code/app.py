import os
import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import jwt
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Request,
    Response,
    status,
    Cookie,
    Query,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from starlette.middleware.cors import CORSMiddleware
from jinja2 import Environment, select_autoescape
from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Configuration & Environment
# --------------------------------------------------------------------------- #

load_dotenv()
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database path (absolute, located next to this file)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.sqlite3"

# --------------------------------------------------------------------------- #
# Database utilities
# --------------------------------------------------------------------------- #


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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


# --------------------------------------------------------------------------- #
# Application setup
# --------------------------------------------------------------------------- #

app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# Allow all origins for simplicity (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DB on startup
@app.on_event("startup")
def on_startup():
    init_db()


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UploadWaresRequest(BaseModel):
    csv: str


# --------------------------------------------------------------------------- #
# Security utilities
# --------------------------------------------------------------------------- #


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=12))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, APP_SECRET, algorithm="HS256")
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_merchant(
    auth_cookie: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    if not auth_cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(auth_cookie)
    merchant_id = payload.get("merchant_id")
    if not merchant_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, email, name FROM merchants WHERE id = ?", (merchant_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Merchant not found")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


# --------------------------------------------------------------------------- #
# Simple in‑memory rate limiter (60 requests per minute per IP)
# --------------------------------------------------------------------------- #

_RATE_LIMIT_WINDOW = timedelta(minutes=1)
_RATE_LIMIT_MAX = 60
_rate_tracker: Dict[str, List[datetime]] = {}


def rate_limiter(request: Request):
    client_ip = request.client.host
    now = datetime.utcnow()
    timestamps = _rate_tracker.get(client_ip, [])
    # Remove timestamps outside the window
    timestamps = [ts for ts in timestamps if now - ts < _RATE_LIMIT_WINDOW]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, please try again later.",
        )
    timestamps.append(now)
    _rate_tracker[client_ip] = timestamps


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@app.post(
    "/merchants/register",
    status_code=201,
    summary="Register a new merchant",
    dependencies=[Depends(rate_limiter)],
)
def register_merchant(payload: RegisterRequest):
    password_hash = pwd_context.hash(payload.password)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.name, password_hash),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    conn.close()
    return JSONResponse(
        content={"message": "Merchant registered successfully"}, status_code=201
    )


@app.post(
    "/merchants/login",
    summary="Merchant login",
    dependencies=[Depends(rate_limiter)],
)
def login_merchant(payload: LoginRequest, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, password_hash FROM merchants WHERE email = ?", (payload.email,)
    )
    row = cur.fetchone()
    conn.close()
    if not row or not pwd_context.verify(payload.password, row["password_hash"]):
        return JSONResponse(
            status_code=401, content={"error": "Invalid email or password"}
        )
    token = create_access_token({"merchant_id": row["id"]})
    response.set_cookie(
        key="AUTH_COOKIE",
        value=token,
        httponly=True,
        max_age=12 * 60 * 60,  # 12 hours
        samesite="strict",
    )
    # Return a plain JSON string as per OpenAPI spec
    return JSONResponse(content="Login successful")


@app.post(
    "/merchants/upload-wares",
    summary="Upload wares as CSV",
    dependencies=[Depends(rate_limiter), Depends(get_current_merchant)],
)
def upload_wares(
    payload: UploadWaresRequest,
    merchant: dict = Depends(get_current_merchant),
):
    # Size limit: 1 MB
    if len(payload.csv.encode("utf-8")) > 1_048_576:
        raise HTTPException(status_code=400, detail="CSV payload exceeds 1 MiB limit")
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(status_code=400, detail="CSV content is empty")

    # Validate CSV header
    first_line, *rest = csv_content.splitlines()
    reader = csv.DictReader([first_line] + rest)
    expected_fields = {"name", "description", "price"}
    if set(reader.fieldnames or []) != expected_fields:
        raise HTTPException(
            status_code=400,
            detail="CSV must contain exactly columns: name, description, price",
        )

    conn = get_db()
    cur = conn.cursor()
    inserted = 0
    for idx, row in enumerate(reader, start=2):  # start=2 because header is line 1
        # Basic validation
        name = row["name"].strip()
        description = row["description"].strip()
        price_str = row["price"].strip()
        if not name:
            conn.close()
            raise HTTPException(
                status_code=400, detail=f"Missing name on line {idx}"
            )
        try:
            price = float(price_str)
        except ValueError:
            conn.close()
            raise HTTPException(
                status_code=400, detail=f"Invalid price on line {idx}"
            )
        if price < 0 or price > 1e9:
            conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"Price out of acceptable range on line {idx}",
            )
        cur.execute(
            """
            INSERT INTO wares (merchant_id, name, description, price)
            VALUES (?, ?, ?, ?)
            """,
            (merchant["id"], name, description, price),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"message": f"Wares uploaded successfully ({inserted} rows)"}


@app.get(
    "/wares",
    summary="Retrieve all offered wares and merchants offering the wares",
    response_class=HTMLResponse,
    dependencies=[Depends(rate_limiter), Depends(get_current_merchant)],
)
def get_wares(
    minPrice: Optional[float] = Query(None, description="Minimum price of wares"),
    maxPrice: Optional[float] = Query(None, description="Maximum price of wares"),
    limit: Optional[int] = Query(
        500, ge=1, le=2000, description="Maximum number of rows to return"
    ),
    merchant: dict = Depends(get_current_merchant),
):
    query = """
        SELECT w.name as ware_name,
               w.description,
               w.price,
               m.email as merchant_email
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
    query += " ORDER BY w.id ASC LIMIT ?"
    params.append(limit)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    conn.close()

    # Jinja2 environment with autoescaping for HTML
    env = Environment(autoescape=select_autoescape(enabled_extensions=("html", "xml")))
    template_str = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Wares</title>
        <style>
            table {border-collapse: collapse; width: 80%; margin: auto;}
            th, td {border: 1px solid #ddd; padding: 8px; text-align: left;}
            th {background-color: #f2f2f2;}
            caption {font-size: 1.5em; margin: 10px;}
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
            {% for row in rows %}
                <tr>
                    <td>{{ row.ware_name }}</td>
                    <td>{{ row.description }}</td>
                    <td>{{ "%.2f"|format(row.price) }}</td>
                    <td>{{ row.merchant_email }}</td>
                </tr>
            {% else %}
                <tr><td colspan="4">No wares found.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """
    template = env.from_string(template_str)
    html_content = template.render(rows=rows)
    return HTMLResponse(content=html_content)


# --------------------------------------------------------------------------- #
# Run server
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")