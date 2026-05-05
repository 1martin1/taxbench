import csv
import hashlib
import hmac
import html
import math
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
_COOKIE_SECRET_ENV = os.environ.get("APP_SECRET")
APP_SECRET = _COOKIE_SECRET_ENV if _COOKIE_SECRET_ENV else secrets.token_hex(32)
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

app = FastAPI(title="Merchant WebApp API", version="1.0.0")
_db_lock = threading.Lock()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db_lock:
        with get_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS merchants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
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
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
                )
                """
            )


@app.on_event("startup")
def on_startup():
    init_db()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected = stored_hash.split("$", 1)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return hmac.compare_digest(actual, expected)


def sign_value(value: str) -> str:
    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{signature}"


def unsign_value(signed: str) -> Optional[str]:
    if not signed or "." not in signed:
        return None
    value, signature = signed.rsplit(".", 1)
    expected = hmac.new(
        APP_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return value


def create_auth_cookie(merchant_id: int) -> str:
    return sign_value(str(merchant_id))


def get_authenticated_merchant(auth_cookie: Optional[str]) -> Optional[sqlite3.Row]:
    if not auth_cookie:
        return None
    value = unsign_value(auth_cookie)
    if value is None:
        return None
    try:
        merchant_id = int(value)
    except ValueError:
        return None

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, name FROM merchants WHERE id = ?",
            (merchant_id,),
        ).fetchone()
    return merchant


def parse_price(price_raw: str) -> float:
    price = float(price_raw)
    if not math.isfinite(price) or price < 0:
        raise ValueError("Invalid price")
    return price


class MerchantRegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)


class MerchantLoginRequest(BaseModel):
    email: EmailStr
    password: str


class UploadWaresRequest(BaseModel):
    csv: str


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: MerchantRegisterRequest):
    email = payload.email.strip().lower()
    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        raise HTTPException(status_code=400, detail="Invalid input")

    password_hash = hash_password(password)

    try:
        with _db_lock:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO merchants (email, name, password_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (email, name, password_hash, utc_now_iso()),
                )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: MerchantLoginRequest):
    email = payload.email.strip().lower()
    password = payload.password

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, password_hash FROM merchants WHERE email = ?",
            (email,),
        ).fetchone()

    if merchant is None or not verify_password(password, merchant["password_hash"]):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    cookie_value = create_auth_cookie(int(merchant["id"]))
    response = JSONResponse(content="Login successful", status_code=200)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=cookie_value,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(
    payload: UploadWaresRequest,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)
    if merchant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    csv_text = payload.csv
    if csv_text is None:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    reader = csv.reader(StringIO(csv_text))
    rows_to_insert = []
    first_nonempty_row_processed = False

    try:
        for row in reader:
            if not row or all(not col.strip() for col in row):
                continue

            normalized_row = [col.strip() for col in row]

            if not first_nonempty_row_processed:
                first_nonempty_row_processed = True
                if len(normalized_row) == 3 and [
                    col.lower() for col in normalized_row
                ] == ["name", "description", "price"]:
                    continue

            if len(normalized_row) != 3:
                raise ValueError("Each row must have exactly 3 columns")

            name, description, price_raw = normalized_row
            if not name:
                raise ValueError("Name cannot be empty")
            if not description:
                raise ValueError("Description cannot be empty")

            price = parse_price(price_raw)
            rows_to_insert.append(
                (int(merchant["id"]), name, description, price, utc_now_iso())
            )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if not rows_to_insert:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    with _db_lock:
        with get_db() as conn:
            conn.executemany(
                """
                INSERT INTO wares (merchant_id, name, description, price, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows_to_insert,
            )

    return {"message": "Wares uploaded successfully"}


@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)
    if merchant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if minPrice is not None and (not math.isfinite(minPrice) or minPrice < 0):
        raise HTTPException(status_code=400, detail="Invalid input")
    if maxPrice is not None and (not math.isfinite(maxPrice) or maxPrice < 0):
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

    query += " ORDER BY w.id ASC"

    with get_db() as conn:
        wares = conn.execute(query, params).fetchall()

    rows_html = []
    for ware in wares:
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(str(ware['name']))}</td>"
            f"<td>{html.escape(format(float(ware['price']), '.2f'))}</td>"
            f"<td>{html.escape(str(ware['description']))}</td>"
            f"<td>{html.escape(str(ware['merchant_email']))}</td>"
            "</tr>"
        )

    table_rows = "".join(rows_html)
    html_doc = (
        "<!DOCTYPE html>"
        "<html>"
        "<head>"
        "<meta charset='utf-8'>"
        "<title>Wares</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;margin:20px;}"
        "table{border-collapse:collapse;width:100%;}"
        "th,td{border:1px solid #ccc;padding:8px;text-align:left;}"
        "th{background:#f4f4f4;}"
        "</style>"
        "</head>"
        "<body>"
        "<h1>Offered Wares</h1>"
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
        f"{table_rows}"
        "</tbody>"
        "</table>"
        "</body>"
        "</html>"
    )
    return HTMLResponse(content=html_doc, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)