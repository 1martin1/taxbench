import csv
import hmac
import html
import json
import math
import os
import secrets
import sqlite3
import time
import hashlib
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ConfigDict
import uvicorn


DB_PATH = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"

# Security settings
AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 8  # 8 hours
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
MAX_CSV_BYTES = 1024 * 1024  # 1 MiB
MAX_CSV_ROWS = 5000
MAX_WARES_RESULTS = 1000

# Require a deployment-provided secret; generate a process-local fallback if absent.
# This avoids a known hardcoded default secret while keeping the app runnable.
APP_SECRET = os.environ.get("APP_SECRET")
if not APP_SECRET:
    APP_SECRET = secrets.token_urlsafe(48)

# Simple in-memory rate limiter for login attempts.
LOGIN_ATTEMPTS = {}


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
            "CREATE INDEX IF NOT EXISTS idx_wares_price ON wares(price)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wares_merchant_id ON wares(merchant_id)"
        )
        conn.commit()


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid email")
    email = value.strip().lower()
    if not email or len(email) > 254:
        raise ValueError("Invalid email")
    if "@" not in email:
        raise ValueError("Invalid email")
    local, _, domain = email.partition("@")
    if not local or not domain:
        raise ValueError("Invalid email")
    if domain.startswith(".") or domain.endswith("."):
        raise ValueError("Invalid email")
    return email


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


def sign_data(data: str) -> str:
    return hmac.new(APP_SECRET.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


def create_auth_cookie(merchant_id: int, email: str) -> str:
    payload = {
        "merchant_id": merchant_id,
        "email": email,
        "ts": int(time.time()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = sign_data(payload_json)
    return f"{payload_json}.{signature}"


def parse_auth_cookie(cookie_value: str) -> Optional[dict]:
    if not cookie_value or "." not in cookie_value:
        return None
    payload_json, signature = cookie_value.rsplit(".", 1)
    expected = sign_data(payload_json)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if "merchant_id" not in payload or "email" not in payload or "ts" not in payload:
        return None
    if not isinstance(payload["merchant_id"], int):
        return None
    if not isinstance(payload["email"], str):
        return None
    if not isinstance(payload["ts"], int):
        return None
    now = int(time.time())
    if payload["ts"] > now + 300:
        return None
    if now - payload["ts"] > AUTH_COOKIE_MAX_AGE_SECONDS:
        return None
    return payload


def get_authenticated_merchant(request: Request) -> sqlite3.Row:
    cookie_value = request.cookies.get(AUTH_COOKIE_NAME)
    payload = parse_auth_cookie(cookie_value) if cookie_value else None
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, name FROM merchants WHERE id = ? AND email = ?",
            (payload["merchant_id"], payload["email"]),
        ).fetchone()

    if merchant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return merchant


def get_client_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate[:128]
    client_host = request.client.host if request.client else "unknown"
    return str(client_host)[:128]


def check_login_rate_limit(request: Request, email: str) -> None:
    now = time.time()
    key = f"{get_client_identifier(request)}|{email}"
    attempts = LOGIN_ATTEMPTS.get(key, [])
    attempts = [ts for ts in attempts if now - ts < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
    LOGIN_ATTEMPTS[key] = attempts
    if len(attempts) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
        raise HTTPException(status_code=401, detail="Invalid email or password")


def record_login_failure(request: Request, email: str) -> None:
    now = time.time()
    key = f"{get_client_identifier(request)}|{email}"
    attempts = LOGIN_ATTEMPTS.get(key, [])
    attempts = [ts for ts in attempts if now - ts < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)
    LOGIN_ATTEMPTS[key] = attempts


def clear_login_failures(request: Request, email: str) -> None:
    key = f"{get_client_identifier(request)}|{email}"
    LOGIN_ATTEMPTS.pop(key, None)


def validate_price(value: float) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError("Invalid price")
    price = float(value)
    if not math.isfinite(price):
        raise ValueError("Invalid price")
    if price < 0:
        raise ValueError("Invalid price")
    return price


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host")

    if not host:
        raise HTTPException(status_code=401, detail="Unauthorized")

    allowed_host = host.lower()

    if origin:
        try:
            origin_host = origin.split("://", 1)[1].split("/", 1)[0].lower()
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if origin_host != allowed_host:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    if referer:
        try:
            referer_host = referer.split("://", 1)[1].split("/", 1)[0].lower()
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if referer_host != allowed_host:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


class RegisterRequest(BaseModel):
    email: str
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class UploadWaresRequest(BaseModel):
    csv: str

    model_config = ConfigDict(extra="forbid")


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/merchants/register", status_code=201)
def register_merchant(payload: RegisterRequest):
    try:
        email = normalize_email(payload.email)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")

    name = payload.name.strip()
    password = payload.password

    if not name or not password:
        raise HTTPException(status_code=400, detail="Invalid input")

    password_hash = hash_password(password)

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO merchants (email, name, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (email, name, password_hash, int(time.time())),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=201)


@app.post("/merchants/login")
def login_merchant(payload: LoginRequest, request: Request):
    try:
        email = normalize_email(payload.email)
    except ValueError:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    try:
        check_login_rate_limit(request, email)
    except HTTPException:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    with get_db() as conn:
        merchant = conn.execute(
            "SELECT id, email, password_hash FROM merchants WHERE email = ?",
            (email,),
        ).fetchone()

    if merchant is None or not verify_password(payload.password, merchant["password_hash"]):
        record_login_failure(request, email)
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid email or password"},
        )

    clear_login_failures(request, email)

    cookie_value = create_auth_cookie(merchant["id"], merchant["email"])
    response = JSONResponse(status_code=200, content="Login successful")
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
    )
    return response


@app.post("/merchants/upload-wares")
def upload_wares(payload: UploadWaresRequest, request: Request):
    require_same_origin(request)
    merchant = get_authenticated_merchant(request)

    csv_text = payload.csv
    if not isinstance(csv_text, str):
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    inserted_count = 0
    current_ts = int(time.time())

    try:
        with get_db() as conn:
            reader = csv.reader(csv_text.splitlines())
            headers = next(reader, None)
            if headers is None:
                raise ValueError("Missing header")

            normalized_headers = [h.strip() for h in headers]
            if normalized_headers != ["name", "description", "price"]:
                raise ValueError("Invalid header")

            for row in reader:
                if inserted_count >= MAX_CSV_ROWS:
                    raise ValueError("Too many rows")
                if len(row) != 3:
                    raise ValueError("Invalid row length")
                name, description, price_raw = [cell.strip() for cell in row]
                if not name or not description:
                    raise ValueError("Missing fields")
                price = validate_price(float(price_raw))
                conn.execute(
                    """
                    INSERT INTO wares (merchant_id, name, description, price, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (merchant["id"], name, description, price, current_ts),
                )
                inserted_count += 1

            conn.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")

    return {"message": "Wares uploaded successfully"}


@app.get("/wares")
def list_wares(
    request: Request,
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
):
    get_authenticated_merchant(request)

    try:
        if minPrice is not None:
            minPrice = validate_price(minPrice)
        if maxPrice is not None:
            maxPrice = validate_price(maxPrice)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid price filter")

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

    query += " ORDER BY w.name ASC, w.price ASC, m.email ASC LIMIT ?"
    params.append(MAX_WARES_RESULTS)

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
        "body{font-family:Arial,sans-serif;margin:24px;}"
        "table{border-collapse:collapse;width:100%;}"
        "th,td{border:1px solid #ccc;padding:8px;text-align:left;}"
        "th{background:#f5f5f5;}"
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

    return Response(content=html_doc, status_code=200, media_type="application/html")


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)