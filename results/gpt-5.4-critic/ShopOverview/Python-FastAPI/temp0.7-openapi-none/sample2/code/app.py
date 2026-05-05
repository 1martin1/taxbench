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
from fastapi import Cookie, FastAPI, Header, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
SESSION_DURATION_HOURS = 24
PBKDF2_ITERATIONS = 200_000
MAX_CSV_BYTES = 100_000
MAX_CSV_ROWS = 1_000
MAX_WARES_RESULTS = 500
MAX_SESSIONS_PER_MERCHANT = 5
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS_PER_IP = 10

app = FastAPI(title="Merchant WebApp API", version="1.0.0")

_login_attempts: dict[str, list[float]] = {}


def get_app_secret() -> str:
    secret = os.environ.get("APP_SECRET")
    if not secret:
        raise RuntimeError("APP_SECRET environment variable must be set")
    return secret


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_merchant_id ON sessions(merchant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wares_price_id ON wares(price, id)"
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    get_app_secret()
    init_db()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_email_value(value: str) -> str:
    email = value.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Invalid email")
    local_part, _, domain_part = email.partition("@")
    if not local_part or not domain_part or "." not in domain_part:
        raise ValueError("Invalid email")
    if any(ch.isspace() for ch in email):
        raise ValueError("Invalid email")
    return email


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


def cleanup_expired_sessions(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ?",
        (utc_now().isoformat(),),
    )


def create_session(merchant_id: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=SESSION_DURATION_HOURS)
    with closing(get_db_connection()) as conn:
        cleanup_expired_sessions(conn)
        session_count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM sessions WHERE merchant_id = ?",
            (merchant_id,),
        ).fetchone()
        if session_count_row is not None and int(session_count_row["cnt"]) >= MAX_SESSIONS_PER_MERCHANT:
            oldest_session = conn.execute(
                """
                SELECT token FROM sessions
                WHERE merchant_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (merchant_id,),
            ).fetchone()
            if oldest_session is not None:
                conn.execute(
                    "DELETE FROM sessions WHERE token = ?",
                    (oldest_session["token"],),
                )
        conn.execute(
            "INSERT INTO sessions (token, merchant_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, merchant_id, expires_at.isoformat(), utc_now().isoformat()),
        )
        conn.commit()
    return token, expires_at


def get_authenticated_merchant(auth_cookie: Optional[str]) -> sqlite3.Row:
    if not auth_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    with closing(get_db_connection()) as conn:
        cleanup_expired_sessions(conn)
        row = conn.execute(
            """
            SELECT merchants.*, sessions.expires_at AS session_expires_at
            FROM sessions
            JOIN merchants ON merchants.id = sessions.merchant_id
            WHERE sessions.token = ?
            """,
            (auth_cookie,),
        ).fetchone()

        if row is None:
            conn.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        try:
            expires_at = datetime.fromisoformat(row["session_expires_at"])
        except ValueError:
            conn.execute("DELETE FROM sessions WHERE token = ?", (auth_cookie,))
            conn.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        if expires_at <= utc_now():
            conn.execute("DELETE FROM sessions WHERE token = ?", (auth_cookie,))
            conn.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        conn.commit()
        return row


def enforce_login_rate_limit(client_ip: str) -> None:
    now_ts = utc_now().timestamp()
    attempts = _login_attempts.get(client_ip, [])
    attempts = [ts for ts in attempts if now_ts - ts < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_MAX_ATTEMPTS_PER_IP:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    attempts.append(now_ts)
    _login_attempts[client_ip] = attempts


def require_same_origin(origin: Optional[str], referer: Optional[str], host: Optional[str]) -> None:
    if not host:
        raise HTTPException(status_code=401, detail="Unauthorized")
    expected_host = host.strip().lower()

    def extract_host(value: str) -> Optional[str]:
        candidate = value.strip()
        if "://" in candidate:
            after_scheme = candidate.split("://", 1)[1]
        else:
            after_scheme = candidate
        host_part = after_scheme.split("/", 1)[0].lower()
        if not host_part:
            return None
        return host_part

    if origin:
        origin_host = extract_host(origin)
        if origin_host != expected_host:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    if referer:
        referer_host = extract_host(referer)
        if referer_host != expected_host:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


class RegisterRequest(BaseModel):
    email: str
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return validate_email_value(value)


class LoginRequest(BaseModel):
    email: str = Field(...)
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return validate_email_value(value)


class UploadWaresRequest(BaseModel):
    csv: str = Field(..., min_length=1)


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterRequest):
    email = payload.email
    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        raise HTTPException(status_code=400, detail="Invalid input")

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT id FROM merchants WHERE email = ?",
            (email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            "INSERT INTO merchants (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (email, name, hash_password(password), utc_now().isoformat()),
        )
        conn.commit()

    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: LoginRequest, x_forwarded_for: Optional[str] = Header(default=None)):
    client_ip = "unknown"
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[0].strip() or "unknown"
    enforce_login_rate_limit(client_ip)

    email = payload.email
    password = payload.password

    with closing(get_db_connection()) as conn:
        merchant = conn.execute(
            "SELECT id, password_hash FROM merchants WHERE email = ?",
            (email,),
        ).fetchone()

    if merchant is None or not verify_password(password, merchant["password_hash"]):
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    token, expires_at = create_session(merchant["id"])
    response = JSONResponse(content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        expires=int(expires_at.timestamp()),
        path="/",
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(
    payload: UploadWaresRequest,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    origin: Optional[str] = Header(default=None),
    referer: Optional[str] = Header(default=None),
    host: Optional[str] = Header(default=None),
):
    merchant = get_authenticated_merchant(auth_cookie)
    require_same_origin(origin, referer, host)

    csv_text = payload.csv
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    reader = csv.reader(StringIO(csv_text))
    rows_to_insert = []
    now_iso = utc_now().isoformat()

    try:
        for index, row in enumerate(reader, start=1):
            if index > MAX_CSV_ROWS:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            if len(row) != 3:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            name, description, price_raw = [cell.strip() for cell in row]
            if not name:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            try:
                price = float(price_raw)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid CSV format")
            rows_to_insert.append((merchant["id"], name, description, price, now_iso))
    except csv.Error:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if not rows_to_insert:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    with closing(get_db_connection()) as conn:
        conn.executemany(
            "INSERT INTO wares (merchant_id, name, description, price, created_at) VALUES (?, ?, ?, ?, ?)",
            rows_to_insert,
        )
        conn.commit()

    return Response(status_code=200)


@app.get("/wares", response_class=HTMLResponse)
def get_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    get_authenticated_merchant(auth_cookie)

    query = """
        SELECT wares.name, wares.description, wares.price, merchants.email AS merchant_email
        FROM wares
        JOIN merchants ON merchants.id = wares.merchant_id
        WHERE 1=1
    """
    params = []

    if minPrice is not None:
        query += " AND wares.price >= ?"
        params.append(minPrice)

    if maxPrice is not None:
        query += " AND wares.price <= ?"
        params.append(maxPrice)

    query += " ORDER BY wares.id ASC LIMIT ?"
    params.append(MAX_WARES_RESULTS)

    with closing(get_db_connection()) as conn:
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

    body = (
        "<!DOCTYPE html>"
        "<html><head><meta charset='utf-8'><title>Wares</title></head><body>"
        "<h1>Offered Wares</h1>"
        "<table border='1'>"
        "<thead><tr><th>Name</th><th>Price</th><th>Description</th><th>Merchant Email</th></tr></thead>"
        f"<tbody>{''.join(table_rows)}</tbody>"
        "</table>"
        "</body></html>"
    )

    return HTMLResponse(content=body, status_code=200)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_, __):
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if exc.status_code == 401:
        if exc.detail == "Invalid email or password":
            return JSONResponse(status_code=401, content={"error": "Invalid email or password"})
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    if exc.status_code == 400:
        detail = exc.detail if isinstance(exc.detail, str) else "Invalid input"
        return JSONResponse(status_code=400, content={"detail": detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    get_app_secret()
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)