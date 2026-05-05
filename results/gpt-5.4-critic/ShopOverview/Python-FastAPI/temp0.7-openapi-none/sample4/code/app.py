import csv
import hashlib
import hmac
import html
import math
import os
import secrets
import sqlite3
import time
from contextlib import closing
from io import StringIO
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import Response as FastAPIResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")

PBKDF2_ITERATIONS = 200_000
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
MAX_CSV_BYTES = 100_000
MAX_CSV_ROWS = 1_000
MAX_WARE_NAME_LENGTH = 255
MAX_WARE_DESCRIPTION_LENGTH = 2_000
MAX_PRICE = 1_000_000_000.0
MAX_WARES_RESULTS = 1_000


app = FastAPI(title="Merchant WebApp API", version="1.0.0")


_login_attempts = {}
_login_attempts_lock = Lock()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL
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
                FOREIGN KEY (merchant_id) REFERENCES merchants(id)
            )
            """
        )
        conn.commit()


def normalize_email(value: str) -> str:
    email = value.strip()
    if not email or "@" not in email:
        raise ValueError("Invalid email")
    if len(email) > 320:
        raise ValueError("Invalid email")
    local_part, _, domain_part = email.rpartition("@")
    if not local_part or not domain_part:
        raise ValueError("Invalid email")
    return f"{local_part}@{domain_part.lower()}"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected = stored_hash.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(digest, expected)


def sign_session(merchant_id: int, issued_at: int) -> str:
    payload = f"{merchant_id}:{issued_at}"
    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_session(token: str) -> Optional[int]:
    try:
        merchant_id_str, issued_at_str, signature = token.split(":", 2)
        payload = f"{merchant_id_str}:{issued_at_str}"
        expected = hmac.new(
            APP_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        merchant_id = int(merchant_id_str)
        issued_at = int(issued_at_str)
        now = int(time.time())
        if issued_at > now or now - issued_at > SESSION_MAX_AGE_SECONDS:
            return None
        return merchant_id
    except (ValueError, TypeError):
        return None


def get_authenticated_merchant_id(request: Request) -> int:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    merchant_id = verify_session(token)
    if merchant_id is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT id FROM merchants WHERE id = ?",
            (merchant_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return merchant_id


def is_login_rate_limited(identifier: str) -> bool:
    now = time.time()
    with _login_attempts_lock:
        attempts = _login_attempts.get(identifier, [])
        attempts = [ts for ts in attempts if now - ts < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
        _login_attempts[identifier] = attempts
        return len(attempts) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS


def record_login_failure(identifier: str) -> None:
    now = time.time()
    with _login_attempts_lock:
        attempts = _login_attempts.get(identifier, [])
        attempts = [ts for ts in attempts if now - ts < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
        attempts.append(now)
        _login_attempts[identifier] = attempts


def clear_login_failures(identifier: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(identifier, None)


def validate_price(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= MAX_PRICE


class MerchantRegisterRequest(BaseModel):
    email: str
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class MerchantLoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class UploadWaresRequest(BaseModel):
    csv: str = Field(min_length=1)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: MerchantRegisterRequest):
    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT id FROM merchants WHERE email = ?",
            (payload.email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Invalid input")
        try:
            conn.execute(
                "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
                (payload.email, payload.name, hash_password(payload.password)),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Invalid input")
    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: MerchantLoginRequest, request: Request):
    client_host = request.client.host if request.client else "unknown"
    identifier = f"{payload.email}|{client_host}"

    if is_login_rate_limited(identifier):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM merchants WHERE email = ?",
            (payload.email,),
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        record_login_failure(identifier)
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    clear_login_failures(identifier)
    token = sign_session(int(row["id"]), int(time.time()))
    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadWaresRequest, request: Request):
    merchant_id = get_authenticated_merchant_id(request)

    if "csv" not in payload.model_fields_set:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    csv_text = payload.csv
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    inserted_rows = 0

    try:
        with closing(get_db()) as conn:
            reader = csv.reader(StringIO(csv_text))
            for row in reader:
                if inserted_rows >= MAX_CSV_ROWS:
                    raise HTTPException(status_code=400, detail="Invalid CSV format")
                if len(row) != 3:
                    raise HTTPException(status_code=400, detail="Invalid CSV format")

                name, description, price_str = row
                name = name.strip()
                description = description.strip()
                price_str = price_str.strip()

                if not name or not description:
                    raise HTTPException(status_code=400, detail="Invalid CSV format")
                if len(name) > MAX_WARE_NAME_LENGTH or len(description) > MAX_WARE_DESCRIPTION_LENGTH:
                    raise HTTPException(status_code=400, detail="Invalid CSV format")

                try:
                    price = float(price_str)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid CSV format")

                if not validate_price(price):
                    raise HTTPException(status_code=400, detail="Invalid CSV format")

                conn.execute(
                    "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
                    (merchant_id, name, description, price),
                )
                inserted_rows += 1

            if inserted_rows == 0:
                raise HTTPException(status_code=400, detail="Invalid CSV format")

            conn.commit()
    except HTTPException:
        raise
    except sqlite3.DatabaseError:
        raise

    return Response(status_code=200)


@app.get(
    "/wares",
    response_class=FastAPIResponse,
    responses={
        200: {
            "description": "List of wares filtered using the provided query and rendered as an HTML table. Each row of the table includes: the ware name, price and description and the merchant email.",
            "content": {
                "application/html": {
                    "schema": {"type": "string"}
                }
            },
        }
    },
)
def list_wares(
    request: Request,
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
):
    get_authenticated_merchant_id(request)

    if minPrice is not None and not validate_price(minPrice):
        raise HTTPException(status_code=400, detail="Invalid input")
    if maxPrice is not None and not validate_price(maxPrice):
        raise HTTPException(status_code=400, detail="Invalid input")

    query = """
        SELECT w.name, w.description, w.price, m.email AS merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
        WHERE 1=1
    """
    params = []

    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)

    query += " ORDER BY w.id ASC LIMIT ?"
    params.append(MAX_WARES_RESULTS)

    with closing(get_db()) as conn:
        rows = conn.execute(query, params).fetchall()

    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row['name']))}</td>"
            f"<td>{html.escape(str(row['price']))}</td>"
            f"<td>{html.escape(str(row['description']))}</td>"
            f"<td>{html.escape(str(row['merchant_email']))}</td>"
            "</tr>"
        )

    html_body = (
        "<!DOCTYPE html>"
        "<html>"
        "<head><meta charset='utf-8'><title>Wares</title></head>"
        "<body>"
        "<h1>Offered Wares</h1>"
        "<table border='1'>"
        "<thead>"
        "<tr>"
        "<th>Ware Name</th>"
        "<th>Price</th>"
        "<th>Description</th>"
        "<th>Merchant Email</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(table_rows) +
        "</tbody>"
        "</table>"
        "</body>"
        "</html>"
    )
    return FastAPIResponse(content=html_body, status_code=200, media_type="application/html")


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)