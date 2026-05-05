import csv
import hashlib
import hmac
import html
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Optional

from fastapi import Cookie, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
APP_SECRET = os.environ.get("APP_SECRET", "development-secret-change-me")
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


app = FastAPI(
    title="Merchant WebApp API",
    description="API for merchants to register, upload wares, and for users to retrieve offered items.",
    version="1.0.0",
)


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


def init_db() -> None:
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


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected = stored_hash.split("$", 1)
    except ValueError:
        return False
    computed = hash_password(password, salt)
    return hmac.compare_digest(computed, stored_hash)


def sign_auth_value(merchant_id: int, expires_ts: int) -> str:
    payload = f"{merchant_id}:{expires_ts}"
    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{merchant_id}:{expires_ts}:{signature}"


def verify_auth_value(cookie_value: str) -> Optional[int]:
    try:
        merchant_id_str, expires_ts_str, signature = cookie_value.split(":", 2)
        merchant_id = int(merchant_id_str)
        expires_ts = int(expires_ts_str)
    except (ValueError, AttributeError):
        return None

    if expires_ts < int(datetime.now(timezone.utc).timestamp()):
        return None

    payload = f"{merchant_id}:{expires_ts}"
    expected_signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    return merchant_id


def get_authenticated_merchant(auth_cookie: Optional[str]) -> sqlite3.Row:
    if not auth_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    merchant_id = verify_auth_value(auth_cookie)
    if merchant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, name FROM merchants WHERE id = ?",
            (merchant_id,),
        ).fetchone()

    if merchant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return merchant


class RegisterMerchantRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginMerchantRequest(BaseModel):
    email: EmailStr
    password: str


class UploadWaresRequest(BaseModel):
    csv: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterMerchantRequest):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM merchants WHERE email = ?",
            (payload.email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid input",
            )

        conn.execute(
            """
            INSERT INTO merchants (email, name, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                payload.email,
                payload.name,
                hash_password(payload.password),
                utcnow_iso(),
            ),
        )

    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: LoginMerchantRequest):
    with get_db() as conn:
        merchant = conn.execute(
            """
            SELECT id, email, password_hash
            FROM merchants
            WHERE email = ?
            """,
            (payload.email,),
        ).fetchone()

    if merchant is None or not verify_password(payload.password, merchant["password_hash"]):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=COOKIE_MAX_AGE)
    cookie_value = sign_auth_value(merchant["id"], int(expires_at.timestamp()))

    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=cookie_value,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(
    payload: UploadWaresRequest,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)

    try:
        reader = csv.DictReader(StringIO(payload.csv))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid CSV format",
        )

    expected_fields = ["name", "description", "price"]
    if reader.fieldnames != expected_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid CSV format",
        )

    rows_to_insert = []
    try:
        for row in reader:
            if row is None:
                raise ValueError

            name = (row.get("name") or "").strip()
            description = (row.get("description") or "").strip()
            price_raw = (row.get("price") or "").strip()

            if not name or not description or not price_raw:
                raise ValueError

            price = float(price_raw)
            if price < 0:
                raise ValueError

            rows_to_insert.append(
                (merchant["id"], name, description, price, utcnow_iso())
            )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid CSV format",
        )

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
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    get_authenticated_merchant(auth_cookie)

    query = """
        SELECT w.name, w.description, w.price, m.email AS merchant_email
        FROM wares w
        JOIN merchants m ON m.id = w.merchant_id
        WHERE 1=1
    """
    params = []

    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)

    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)

    query += " ORDER BY w.name ASC, w.price ASC, m.email ASC"

    with get_db() as conn:
        wares = conn.execute(query, params).fetchall()

    rows_html = []
    for ware in wares:
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(str(ware['name']))}</td>"
            f"<td>{html.escape(f'{ware['price']:.2f}')}</td>"
            f"<td>{html.escape(str(ware['description']))}</td>"
            f"<td>{html.escape(str(ware['merchant_email']))}</td>"
            "</tr>"
        )

    if not rows_html:
        rows_html.append(
            '<tr><td colspan="4">No wares found</td></tr>'
        )

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
        "th{background:#f2f2f2;}"
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
        + "".join(rows_html) +
        "</tbody>"
        "</table>"
        "</body>"
        "</html>"
    )

    return HTMLResponse(content=html_doc, status_code=200)


if __name__ == "__main__":
    import uvicorn

    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)