import csv
import hashlib
import hmac
import html
import io
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"

PASSWORD_PBKDF2_ITERATIONS = 200_000
AUTH_COOKIE_MAX_AGE = 3600
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10

MAX_EMAIL_LENGTH = 254
MAX_MERCHANT_NAME_LENGTH = 200
MAX_PASSWORD_LENGTH = 1024
MAX_CSV_PAYLOAD_SIZE = 1_000_000
MAX_WARE_NAME_LENGTH = 200
MAX_WARE_DESCRIPTION_LENGTH = 5000
MAX_UPLOAD_ROWS = 1000
MAX_WARES_LIST_ROWS = 1000

_login_attempts: dict[str, list[float]] = {}


def _load_app_secret() -> bytes:
    secret = os.environ.get("APP_SECRET")
    if not secret:
        raise RuntimeError("APP_SECRET environment variable must be set")
    return secret.encode("utf-8")


APP_SECRET = _load_app_secret()

app = FastAPI(title="Merchant WebApp API", version="1.0.0")


class ApplicationHTMLResponse(HTMLResponse):
    media_type = "application/html"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price REAL NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (merchant_id) REFERENCES merchants(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_merchants_email ON merchants(email)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wares_price_id ON wares(price, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wares_merchant_id ON wares(merchant_id)"
        )


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_PBKDF2_ITERATIONS,
    ).hex()


def create_password_record(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)
    return salt, password_hash


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    calculated = hash_password(password, salt)
    return hmac.compare_digest(calculated, stored_hash)


def sign_value(value: str) -> str:
    return hmac.new(APP_SECRET, value.encode("utf-8"), hashlib.sha256).hexdigest()


def create_auth_cookie(merchant_id: int, issued_at: int) -> str:
    payload = f"{merchant_id}:{issued_at}"
    signature = sign_value(payload)
    return f"{payload}.{signature}"


def verify_auth_cookie(cookie_value: Optional[str]) -> Optional[int]:
    if not cookie_value or "." not in cookie_value:
        return None

    payload, signature = cookie_value.rsplit(".", 1)
    expected = sign_value(payload)
    if not hmac.compare_digest(signature, expected):
        return None

    if ":" not in payload:
        return None

    merchant_id_str, issued_at_str = payload.split(":", 1)
    try:
        merchant_id = int(merchant_id_str)
        issued_at = int(issued_at_str)
    except ValueError:
        return None

    now = int(time.time())
    if issued_at > now or now - issued_at > AUTH_COOKIE_MAX_AGE:
        return None

    return merchant_id


def get_authenticated_merchant(auth_cookie: Optional[str]) -> sqlite3.Row:
    merchant_id = verify_auth_cookie(auth_cookie)
    if merchant_id is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, name FROM merchants WHERE id = ?",
            (merchant_id,),
        ).fetchone()

    if merchant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return merchant


def normalize_email(value: str) -> str:
    email_value = value.strip()
    if not email_value or len(email_value) > MAX_EMAIL_LENGTH:
        raise ValueError("Invalid email")

    if email_value.count("@") != 1:
        raise ValueError("Invalid email")

    local_part, domain_part = email_value.rsplit("@", 1)
    if not local_part or not domain_part:
        raise ValueError("Invalid email")
    if len(local_part) > 64:
        raise ValueError("Invalid email")
    if "." not in domain_part:
        raise ValueError("Invalid email")
    if domain_part.startswith(".") or domain_part.endswith("."):
        raise ValueError("Invalid email")
    if any(ch.isspace() for ch in email_value):
        raise ValueError("Invalid email")

    return email_value.lower()


def check_login_rate_limit(identifier: str):
    now = time.time()
    attempts = _login_attempts.get(identifier, [])
    attempts = [ts for ts in attempts if now - ts < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _login_attempts[identifier] = attempts


def record_login_failure(identifier: str):
    now = time.time()
    attempts = _login_attempts.get(identifier, [])
    attempts = [ts for ts in attempts if now - ts < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)
    _login_attempts[identifier] = attempts


def clear_login_failures(identifier: str):
    _login_attempts.pop(identifier, None)


class RegisterRequest(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=MAX_MERCHANT_NAME_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Invalid input")
        return cleaned


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class UploadWaresRequest(BaseModel):
    csv: Optional[str] = None

    @field_validator("csv")
    @classmethod
    def validate_csv_size(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if len(value) > MAX_CSV_PAYLOAD_SIZE:
            raise ValueError("Invalid CSV format")
        return value


@app.on_event("startup")
def startup_event():
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterRequest):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM merchants WHERE email = ?",
            (payload.email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Invalid input")

        salt, password_hash = create_password_record(payload.password)
        conn.execute(
            """
            INSERT INTO merchants (email, name, password_hash, password_salt, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.email,
                payload.name,
                password_hash,
                salt,
                int(time.time()),
            ),
        )
    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: LoginRequest):
    identifier = payload.email
    check_login_rate_limit(identifier)

    with get_db() as conn:
        merchant = conn.execute(
            """
            SELECT id, email, password_hash, password_salt
            FROM merchants
            WHERE email = ?
            """,
            (payload.email,),
        ).fetchone()

    if merchant is None or not verify_password(
        payload.password,
        merchant["password_salt"],
        merchant["password_hash"],
    ):
        record_login_failure(identifier)
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    clear_login_failures(identifier)
    issued_at = int(time.time())
    cookie_value = create_auth_cookie(merchant["id"], issued_at)
    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE,
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(
    payload: UploadWaresRequest,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)

    if payload.csv is None:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    try:
        csv_stream = io.StringIO(payload.csv)
        reader = csv.reader(csv_stream)
        header = next(reader, None)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if header is None:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    normalized_header = [cell.strip().lower() for cell in header]
    if normalized_header != ["name", "description", "price"]:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    parsed_rows = []
    now = int(time.time())
    row_count = 0

    try:
        for row in reader:
            row_count += 1
            if row_count > MAX_UPLOAD_ROWS:
                raise HTTPException(status_code=400, detail="Invalid CSV format")

            if len(row) != 3:
                raise HTTPException(status_code=400, detail="Invalid CSV format")

            name, description, price_str = row
            name = name.strip()
            description = description.strip()
            price_str = price_str.strip()

            if not name:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            if len(name) > MAX_WARE_NAME_LENGTH:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            if len(description) > MAX_WARE_DESCRIPTION_LENGTH:
                raise HTTPException(status_code=400, detail="Invalid CSV format")

            try:
                price = float(price_str)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid CSV format")

            parsed_rows.append((merchant["id"], name, description, price, now))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if not parsed_rows:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO wares (merchant_id, name, description, price, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            parsed_rows,
        )

    return {"message": "Wares uploaded successfully"}


@app.get("/wares", response_class=ApplicationHTMLResponse)
def list_wares(
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    get_authenticated_merchant(auth_cookie)

    query = """
        SELECT w.name, w.description, w.price, m.email AS merchant_email
        FROM wares w
        JOIN merchants m ON m.id = w.merchant_id
        WHERE 1 = 1
    """
    params = []

    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)

    query += " ORDER BY w.id ASC LIMIT ?"
    params.append(MAX_WARES_LIST_ROWS)

    with get_db() as conn:
        wares = conn.execute(query, params).fetchall()

    rows = []
    for ware in wares:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(ware['name']))}</td>"
            f"<td>{html.escape(str(ware['price']))}</td>"
            f"<td>{html.escape(str(ware['description']))}</td>"
            f"<td>{html.escape(str(ware['merchant_email']))}</td>"
            "</tr>"
        )

    rows_html = "".join(rows)
    if not rows_html:
        rows_html = '<tr><td colspan="4">No wares found</td></tr>'

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Wares</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 2rem;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
        }}
        th, td {{
            border: 1px solid #ccc;
            padding: 0.5rem;
            text-align: left;
        }}
        th {{
            background: #f2f2f2;
        }}
    </style>
</head>
<body>
    <h1>Available Wares</h1>
    <table>
        <thead>
            <tr>
                <th>Ware Name</th>
                <th>Price</th>
                <th>Description</th>
                <th>Merchant Email</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</body>
</html>"""
    return ApplicationHTMLResponse(content=page)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)