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
from math import isfinite
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"

# Security-related limits
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 255
MAX_PASSWORD_LENGTH = 1024
MAX_CSV_LENGTH = 1_000_000
MAX_CSV_ROWS = 10_000
MAX_WARE_NAME_LENGTH = 255
MAX_WARE_DESCRIPTION_LENGTH = 5000

PBKDF2_ITERATIONS = 200_000
AUTH_COOKIE_MAX_AGE = 60 * 60 * 8  # 8 hours

RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS_PER_WINDOW = 10
AUTH_FAILURE_DELAY_SECONDS = 0.2

# Require an application secret from the environment for secure cookie signing.
APP_SECRET = os.environ.get("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")


app = FastAPI(title="Merchant WebApp API", version="1.0.0")

_LOGIN_ATTEMPTS = {}


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
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
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


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


def sign_value(value: str, expires_at: int) -> str:
    payload = f"{value}|{expires_at}"
    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def unsign_value(signed_value: str) -> Optional[str]:
    if "." not in signed_value:
        return None
    payload, signature = signed_value.rsplit(".", 1)
    expected = hmac.new(
        APP_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    if "|" not in payload:
        return None

    value, expires_raw = payload.rsplit("|", 1)
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return None

    if expires_at < int(time.time()):
        return None

    return value


def get_authenticated_merchant_id(request: Request) -> Optional[int]:
    cookie = request.cookies.get(AUTH_COOKIE_NAME)
    if not cookie:
        return None
    value = unsign_value(cookie)
    if value is None:
        return None
    try:
        merchant_id = int(value)
    except ValueError:
        return None
    return merchant_id


def require_authenticated_merchant_id(request: Request) -> int:
    merchant_id = get_authenticated_merchant_id(request)
    if merchant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return merchant_id


def normalize_email(value: str) -> str:
    return value.strip().lower()


def validate_email_basic(value: str) -> str:
    email = normalize_email(value)
    if not email or len(email) > MAX_EMAIL_LENGTH:
        raise ValueError("Invalid email")
    if "@" not in email or email.count("@") != 1:
        raise ValueError("Invalid email")
    local, domain = email.split("@", 1)
    if not local or not domain:
        raise ValueError("Invalid email")
    if domain.startswith(".") or domain.endswith(".") or "." not in domain:
        raise ValueError("Invalid email")
    return email


def get_client_identifier(request: Request, email: str = "") -> str:
    client_host = request.client.host if request.client and request.client.host else "unknown"
    normalized_email = normalize_email(email) if email else ""
    return f"{client_host}|{normalized_email}"


def prune_login_attempts(now: float) -> None:
    expired_keys = []
    for key, timestamps in _LOGIN_ATTEMPTS.items():
        recent = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SECONDS]
        if recent:
            _LOGIN_ATTEMPTS[key] = recent
        else:
            expired_keys.append(key)
    for key in expired_keys:
        _LOGIN_ATTEMPTS.pop(key, None)


def is_rate_limited(request: Request, email: str = "") -> bool:
    now = time.time()
    prune_login_attempts(now)
    key = get_client_identifier(request, email)
    attempts = _LOGIN_ATTEMPTS.get(key, [])
    return len(attempts) >= LOGIN_MAX_ATTEMPTS_PER_WINDOW


def record_login_attempt(request: Request, email: str = "") -> None:
    now = time.time()
    prune_login_attempts(now)
    key = get_client_identifier(request, email)
    attempts = _LOGIN_ATTEMPTS.get(key, [])
    attempts.append(now)
    _LOGIN_ATTEMPTS[key] = attempts


class MerchantRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return validate_email_basic(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Invalid name")
        return value


class MerchantLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Optional[str] = None
    password: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_email_basic(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError("Invalid password")
        return value


class UploadWaresRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv: Optional[str] = None

    @field_validator("csv")
    @classmethod
    def validate_csv_size(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if len(value) > MAX_CSV_LENGTH:
            raise ValueError("Invalid CSV format")
        return value


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/merchants/register", status_code=201)
async def register_merchant(payload: MerchantRegisterRequest):
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM merchants WHERE email = ?", (payload.email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (payload.email, payload.name, hash_password(payload.password)),
        )
        conn.commit()

    return Response(status_code=201)


@app.post("/merchants/login")
async def login_merchant(payload: MerchantLoginRequest, request: Request):
    email = payload.email or ""
    password = payload.password

    if is_rate_limited(request, email):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    if payload.email is None or password is None:
        time.sleep(AUTH_FAILURE_DELAY_SECONDS)
        record_login_attempt(request, email)
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, password_hash FROM merchants WHERE email = ?",
            (payload.email,),
        ).fetchone()

    if merchant is None or not verify_password(password, merchant["password_hash"]):
        time.sleep(AUTH_FAILURE_DELAY_SECONDS)
        record_login_attempt(request, payload.email)
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    expires_at = int(time.time()) + AUTH_COOKIE_MAX_AGE
    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=sign_value(str(merchant["id"]), expires_at),
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE,
    )
    return response


@app.post("/merchants/upload-wares")
async def upload_wares(payload: UploadWaresRequest, request: Request):
    merchant_id = require_authenticated_merchant_id(request)

    if payload.csv is None:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    inserted_any = False
    row_count = 0

    try:
        csv_stream = io.StringIO(payload.csv)
        reader = csv.reader(csv_stream)

        with get_db() as conn:
            for row in reader:
                if not row:
                    continue

                row_count += 1
                if row_count > MAX_CSV_ROWS:
                    raise ValueError("Too many rows")

                if len(row) != 3:
                    raise ValueError("Each row must have exactly 3 columns")

                name, description, price_raw = [cell.strip() for cell in row]

                if not name or not description:
                    raise ValueError("Name and description are required")
                if len(name) > MAX_WARE_NAME_LENGTH or len(description) > MAX_WARE_DESCRIPTION_LENGTH:
                    raise ValueError("Field too long")

                price = float(price_raw)
                if not isfinite(price) or price < 0:
                    raise ValueError("Invalid price")

                conn.execute(
                    "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
                    (merchant_id, name, description, price),
                )
                inserted_any = True

            if not inserted_any:
                raise ValueError("No valid rows")

            conn.commit()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    return {"message": "Wares uploaded successfully"}


@app.get("/wares", response_class=HTMLResponse)
async def list_wares(minPrice: Optional[float] = None, maxPrice: Optional[float] = None):
    if minPrice is not None and (not isfinite(minPrice)):
        raise HTTPException(status_code=400, detail="Invalid price filter")
    if maxPrice is not None and (not isfinite(maxPrice)):
        raise HTTPException(status_code=400, detail="Invalid price filter")

    query = """
        SELECT wares.name AS ware_name,
               wares.description AS ware_description,
               wares.price AS ware_price,
               merchants.email AS merchant_email
        FROM wares
        JOIN merchants ON wares.merchant_id = merchants.id
        WHERE 1 = 1
    """
    params = []

    if minPrice is not None:
        query += " AND wares.price >= ?"
        params.append(minPrice)

    if maxPrice is not None:
        query += " AND wares.price <= ?"
        params.append(maxPrice)

    query += " ORDER BY wares.id ASC"

    with get_db() as conn:
        wares = conn.execute(query, tuple(params)).fetchall()

    rows_html = []
    for ware in wares:
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(str(ware['ware_name']))}</td>"
            f"<td>{html.escape(str(ware['ware_price']))}</td>"
            f"<td>{html.escape(str(ware['ware_description']))}</td>"
            f"<td>{html.escape(str(ware['merchant_email']))}</td>"
            "</tr>"
        )

    if not rows_html:
        rows_html.append('<tr><td colspan="4">No wares found</td></tr>')

    page = (
        "<!DOCTYPE html>"
        "<html>"
        "<head>"
        "<meta charset='utf-8'>"
        "<title>Wares</title>"
        "<style>"
        "body { font-family: Arial, sans-serif; margin: 2rem; }"
        "table { border-collapse: collapse; width: 100%; }"
        "th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }"
        "th { background: #f4f4f4; }"
        "</style>"
        "</head>"
        "<body>"
        "<h1>Warehouse Wares</h1>"
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Ware Name</th>"
        "<th>Price</th>"
        "<th>Description</th>"
        "<th>Merchant Email</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(rows_html) +
        "</tbody>"
        "</table>"
        "</body>"
        "</html>"
    )
    return HTMLResponse(content=page, status_code=200)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, __: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)