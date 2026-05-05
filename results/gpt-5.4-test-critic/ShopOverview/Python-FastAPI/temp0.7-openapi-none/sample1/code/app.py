import csv
import hashlib
import hmac
import html
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Optional

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
APP_SECRET = os.environ.get("APP_SECRET", "development-secret-change-me").encode("utf-8")


app = FastAPI(title="Merchant WebApp API", version="1.0.0")


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
                FOREIGN KEY (merchant_id) REFERENCES merchants(id)
            )
            """
        )
        conn.commit()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return hmac.compare_digest(digest, expected)


def sign_value(value: str) -> str:
    return hmac.new(APP_SECRET, value.encode("utf-8"), hashlib.sha256).hexdigest()


def make_auth_cookie(merchant_id: int) -> str:
    expires = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())
    payload = f"{merchant_id}:{expires}"
    signature = sign_value(payload)
    return f"{merchant_id}:{expires}:{signature}"


def parse_auth_cookie(cookie_value: Optional[str]) -> Optional[int]:
    if not cookie_value:
        return None
    parts = cookie_value.split(":")
    if len(parts) != 3:
        return None
    merchant_id_str, expires_str, signature = parts
    payload = f"{merchant_id_str}:{expires_str}"
    expected = sign_value(payload)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        merchant_id = int(merchant_id_str)
        expires = int(expires_str)
    except ValueError:
        return None
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if expires < now_ts:
        return None
    return merchant_id


def get_authenticated_merchant(auth_cookie: Optional[str]) -> sqlite3.Row:
    merchant_id = parse_auth_cookie(auth_cookie)
    if merchant_id is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    with closing(get_db()) as conn:
        merchant = conn.execute(
            "SELECT id, email, name FROM merchants WHERE id = ?",
            (merchant_id,),
        ).fetchone()
    if merchant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return merchant


class MerchantRegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)


class MerchantLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UploadWaresRequest(BaseModel):
    csv: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: MerchantRegisterRequest):
    email = payload.email.strip().lower()
    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        raise HTTPException(status_code=400, detail="Invalid input")

    password_hash = hash_password(password)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(get_db()) as conn:
            conn.execute(
                """
                INSERT INTO merchants (email, name, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (email, name, password_hash, created_at),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: MerchantLoginRequest):
    email = payload.email.strip().lower()
    password = payload.password

    with closing(get_db()) as conn:
        merchant = conn.execute(
            """
            SELECT id, email, password_hash
            FROM merchants
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

    if merchant is None or not verify_password(password, merchant["password_hash"]):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    cookie_value = make_auth_cookie(merchant["id"])
    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=7 * 24 * 60 * 60,
        path="/",
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(
    payload: UploadWaresRequest,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)

    csv_text = payload.csv
    if csv_text is None:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    try:
        reader = csv.DictReader(StringIO(csv_text))
        expected_fields = ["name", "description", "price"]
        if reader.fieldnames != expected_fields:
            raise ValueError("Invalid headers")

        rows_to_insert = []
        for row in reader:
            if row is None:
                continue
            name = (row.get("name") or "").strip()
            description = (row.get("description") or "").strip()
            price_raw = (row.get("price") or "").strip()

            if not name or not description or not price_raw:
                raise ValueError("Missing values")

            try:
                price = float(price_raw)
            except ValueError:
                raise ValueError("Invalid price")

            if price < 0:
                raise ValueError("Negative price")

            rows_to_insert.append((merchant["id"], name, description, price, datetime.now(timezone.utc).isoformat()))

        if not rows_to_insert:
            raise ValueError("No rows")

    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    with closing(get_db()) as conn:
        conn.executemany(
            """
            INSERT INTO wares (merchant_id, name, description, price, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        conn.commit()

    return {"message": "Wares uploaded successfully"}


@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    get_authenticated_merchant(auth_cookie)

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

    with closing(get_db()) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

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

    if not table_rows:
        table_rows.append('<tr><td colspan="4">No wares found</td></tr>')

    html_doc = (
        "<!DOCTYPE html>"
        "<html>"
        "<head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Wares</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;margin:2rem;}"
        "table{border-collapse:collapse;width:100%;}"
        "th,td{border:1px solid #ccc;padding:8px;text-align:left;}"
        "th{background:#f5f5f5;}"
        "</style>"
        "</head>"
        "<body>"
        "<h1>Available Wares</h1>"
        "<table>"
        "<thead>"
        "<tr>"
        "<th>Name</th>"
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
    return HTMLResponse(content=html_doc, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)