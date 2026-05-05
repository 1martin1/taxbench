import csv
import hashlib
import hmac
import html
import io
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
APP_SECRET = os.environ.get("APP_SECRET", "development-secret-change-me")
SESSION_DURATION_HOURS = 24


app = FastAPI(title="Merchant WebApp API", version="1.0.0")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
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
                FOREIGN KEY (merchant_id) REFERENCES merchants(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                merchant_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (merchant_id) REFERENCES merchants(id)
            )
            """
        )
        conn.commit()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return hmac.compare_digest(actual, expected)


def create_session(merchant_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + timedelta(hours=SESSION_DURATION_HOURS)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sessions (token, merchant_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, merchant_id, expires_at.isoformat(), now.isoformat()),
        )
        conn.commit()
    return token


def get_authenticated_merchant(auth_cookie: Optional[str]):
    if not auth_cookie:
        return None

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT m.id, m.email, m.name, s.expires_at
            FROM sessions s
            JOIN merchants m ON m.id = s.merchant_id
            WHERE s.token = ?
            """,
            (auth_cookie,),
        ).fetchone()

        if not row:
            return None

        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            conn.execute("DELETE FROM sessions WHERE token = ?", (auth_cookie,))
            conn.commit()
            return None

        if expires_at <= utc_now():
            conn.execute("DELETE FROM sessions WHERE token = ?", (auth_cookie,))
            conn.commit()
            return None

        return {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
        }


class MerchantRegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)


class MerchantLoginRequest(BaseModel):
    email: EmailStr
    password: str


class UploadWaresRequest(BaseModel):
    csv: str


@app.on_event("startup")
def startup_event():
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: MerchantRegisterRequest):
    email = payload.email.strip().lower()
    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        raise HTTPException(status_code=400, detail="Invalid input")

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM merchants WHERE email = ?",
            (email,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            """
            INSERT INTO merchants (email, name, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (email, name, hash_password(password), utc_now().isoformat()),
        )
        conn.commit()

    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: MerchantLoginRequest):
    email = payload.email.strip().lower()

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, password_hash
            FROM merchants
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

    if not row or not verify_password(payload.password, row["password_hash"]):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    token = create_session(row["id"])
    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_DURATION_HOURS * 3600,
        path="/",
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(
    payload: UploadWaresRequest,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)
    if not merchant:
        raise HTTPException(status_code=401, detail="Unauthorized")

    csv_content = payload.csv
    if csv_content is None:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    try:
        reader = csv.reader(io.StringIO(csv_content))
        rows = list(reader)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if not rows:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    parsed_rows = []
    start_index = 0

    first_row = rows[0]
    normalized_header = [cell.strip().lower() for cell in first_row]
    if normalized_header == ["name", "description", "price"]:
        start_index = 1

    for row in rows[start_index:]:
        if len(row) != 3:
            raise HTTPException(status_code=400, detail="Invalid CSV format")

        name = row[0].strip()
        description = row[1].strip()
        price_raw = row[2].strip()

        if not name or not description:
            raise HTTPException(status_code=400, detail="Invalid CSV format")

        try:
            price = float(price_raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid CSV format")

        parsed_rows.append((merchant["id"], name, description, price, utc_now().isoformat()))

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
        conn.commit()

    return {"message": "Wares uploaded successfully"}


@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    merchant = get_authenticated_merchant(auth_cookie)
    if not merchant:
        raise HTTPException(status_code=401, detail="Unauthorized")

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

    query += " ORDER BY w.id ASC"

    with get_db() as conn:
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

    body_rows = "".join(table_rows)
    if not body_rows:
        body_rows = '<tr><td colspan="4">No wares found</td></tr>'

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
        f"<tbody>{body_rows}</tbody>"
        "</table>"
        "</body>"
        "</html>"
    )

    return HTMLResponse(content=html_doc, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)