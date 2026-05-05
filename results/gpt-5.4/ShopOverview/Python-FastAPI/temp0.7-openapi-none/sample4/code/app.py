import csv
import hashlib
import hmac
import html
import os
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
APP_SECRET = os.environ.get("APP_SECRET", "change-me-in-production")


app = FastAPI(title="Merchant WebApp API", version="1.0.0")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL
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
        yield conn
        conn.commit()
    finally:
        conn.close()


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()


def create_password_hash(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    return hash_password(password, salt), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    computed = hash_password(password, salt)
    return hmac.compare_digest(computed, password_hash)


def sign_value(value: str) -> str:
    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{signature}"


def unsign_value(signed_value: str) -> Optional[str]:
    if not signed_value or "." not in signed_value:
        return None
    value, signature = signed_value.rsplit(".", 1)
    expected = hmac.new(
        APP_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if hmac.compare_digest(signature, expected):
        return value
    return None


def get_authenticated_merchant(auth_cookie: Optional[str]) -> Optional[sqlite3.Row]:
    merchant_id_str = unsign_value(auth_cookie or "")
    if not merchant_id_str or not merchant_id_str.isdigit():
        return None
    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, name FROM merchants WHERE id = ?",
            (int(merchant_id_str),),
        ).fetchone()
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
def startup_event() -> None:
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterMerchantRequest):
    password_hash, password_salt = create_password_hash(payload.password)
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO merchants (email, name, password_hash, password_salt)
                VALUES (?, ?, ?, ?)
                """,
                (payload.email, payload.name, password_hash, password_salt),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: LoginMerchantRequest):
    with get_db() as conn:
        merchant = conn.execute(
            """
            SELECT id, email, password_hash, password_salt
            FROM merchants
            WHERE email = ?
            """,
            (payload.email,),
        ).fetchone()

    if not merchant or not verify_password(
        payload.password, merchant["password_hash"], merchant["password_salt"]
    ):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    response = JSONResponse(status_code=200, content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=sign_value(str(merchant["id"])),
        httponly=True,
        samesite="lax",
        secure=False,
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

    rows_to_insert = []
    try:
        reader = csv.reader(payload.csv.splitlines())
        for row in reader:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("Each row must have exactly 3 columns")
            name = row[0].strip()
            description = row[1].strip()
            price_str = row[2].strip()
            if not name:
                raise ValueError("Name is required")
            if not description:
                raise ValueError("Description is required")
            price = float(price_str)
            rows_to_insert.append((merchant["id"], name, description, price))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if not rows_to_insert:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO wares (merchant_id, name, description, price)
            VALUES (?, ?, ?, ?)
            """,
            rows_to_insert,
        )

    return {"message": "Wares uploaded successfully"}


@app.get("/wares", response_class=HTMLResponse)
def get_wares(
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)
    if merchant is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    query = """
        SELECT w.name, w.description, w.price, m.email AS merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
        WHERE 1 = 1
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
        wares = conn.execute(query, tuple(params)).fetchall()

    rows_html = []
    for ware in wares:
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(str(ware['name']))}</td>"
            f"<td>{html.escape(str(ware['price']))}</td>"
            f"<td>{html.escape(str(ware['description']))}</td>"
            f"<td>{html.escape(str(ware['merchant_email']))}</td>"
            "</tr>"
        )

    html_content = (
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
        + "".join(rows_html)
        + "</tbody>"
        "</table>"
        "</body>"
        "</html>"
    )

    return HTMLResponse(content=html_content, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)