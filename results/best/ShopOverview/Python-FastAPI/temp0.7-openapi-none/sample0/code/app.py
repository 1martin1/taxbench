import os
import sqlite3
import time
import csv
from io import StringIO
from html import escape
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from passlib.hash import pbkdf2_sha256
import jwt  # pyjwt

DB_PATH = "db.sqlite3"
APP_SECRET = os.environ.get("APP_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
COOKIE_NAME = "AUTH_COOKIE"


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = connect_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
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
                FOREIGN KEY(merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
            );
        """
        )
        conn.commit()
    finally:
        conn.close()


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UploadWaresRequest(BaseModel):
    csv: str = Field(min_length=1)


def create_token(email: str, ttl_seconds: int = 7 * 24 * 3600) -> str:
    now = int(time.time())
    payload = {
        "sub": email,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    token = jwt.encode(payload, APP_SECRET, algorithm=JWT_ALG)
    return token


def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[JWT_ALG])
        sub = payload.get("sub")
        if not sub:
            raise jwt.InvalidTokenError("Missing subject")
        return sub
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_merchant():
    def dependency(response: Response = None, AUTH_COOKIE: Optional[str] = None):
        # FastAPI doesn't inject cookies directly via default parameters, so we will read later in a dedicated dependency
        # This inner function will be replaced by the real dependency below.
        pass

    return dependency


from fastapi import Cookie  # after fastapi import to create cookie dependency


def current_merchant_dependency(auth_cookie: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)):
    if not auth_cookie:
        raise HTTPException(status_code=401, detail="Unauthorized")
    email = decode_token(auth_cookie)
    conn = connect_db()
    try:
        cur = conn.execute("SELECT id, email, name FROM merchants WHERE email = ?", (email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {"id": row["id"], "email": row["email"], "name": row["name"]}
    finally:
        conn.close()


app = FastAPI(title="Merchant WebApp API", description="API for merchants to register, upload wares, and for users to retrieve offered items.", version="1.0.0")


@app.on_event("startup")
def on_startup():
    init_db()


@app.post("/merchants/register", status_code=201, summary="Register a new merchant")
def register_merchant(data: RegisterRequest):
    conn = connect_db()
    try:
        password_hash = pbkdf2_sha256.hash(data.password)
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                "INSERT INTO merchants (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (str(data.email), data.name.strip(), password_hash, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # likely duplicate email or constraint violation
            raise HTTPException(status_code=400, detail="Invalid input")
        return {"message": "Merchant registered successfully"}
    finally:
        conn.close()


@app.post("/merchants/login", summary="Merchant login")
def merchant_login(data: LoginRequest):
    conn = connect_db()
    try:
        cur = conn.execute(
            "SELECT id, email, name, password_hash FROM merchants WHERE email = ?",
            (str(data.email),),
        )
        row = cur.fetchone()
        if not row:
            return JSONResponse(status_code=401, content={"error": "Invalid email or password"})
        if not pbkdf2_sha256.verify(data.password, row["password_hash"]):
            return JSONResponse(status_code=401, content={"error": "Invalid email or password"})
        token = create_token(row["email"])
        resp = JSONResponse(content="Login successful")
        # Set cookie according to securitySchemes cookieAuth
        resp.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
            max_age=7 * 24 * 3600,
        )
        return resp
    finally:
        conn.close()


def parse_wares_csv(csv_text: str):
    f = StringIO(csv_text.strip())
    reader = csv.reader(f)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError("Empty CSV")
    start_idx = 0
    first = [c.strip().lower() for c in rows[0]]
    if len(first) >= 3 and first[0] == "name" and first[1] == "description" and first[2] == "price":
        start_idx = 1
    wares = []
    for i in range(start_idx, len(rows)):
        r = rows[i]
        if len(r) < 3:
            raise ValueError(f"Row {i+1} must have at least 3 columns")
        name = r[0].strip()
        description = r[1].strip()
        price_str = r[2].strip()
        if not name:
            raise ValueError(f"Row {i+1} name is required")
        if not description:
            raise ValueError(f"Row {i+1} description is required")
        try:
            price = float(price_str)
        except ValueError:
            raise ValueError(f"Row {i+1} price must be a number")
        wares.append((name, description, price))
    if not wares:
        raise ValueError("No data rows found")
    return wares


@app.post("/merchants/upload-wares", summary='Lets a merchant upload their wares as a CSV string with the following columns: name, description, price')
def upload_wares(payload: UploadWaresRequest, merchant=Depends(current_merchant_dependency)):
    try:
        wares = parse_wares_csv(payload.csv)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    conn = connect_db()
    try:
        conn.executemany(
            "INSERT INTO wares (merchant_id, name, description, price) VALUES (?, ?, ?, ?)",
            [(merchant["id"], w[0], w[1], w[2]) for w in wares],
        )
        conn.commit()
        return {"message": "Wares uploaded successfully", "inserted": len(wares)}
    finally:
        conn.close()


@app.get("/wares", summary="Retrieve all offered wares and merchants offering the wares, filtered with the provided query")
def list_wares(minPrice: Optional[float] = None, maxPrice: Optional[float] = None, merchant=Depends(current_merchant_dependency)):
    where = []
    params = []
    if minPrice is not None:
        where.append("w.price >= ?")
        params.append(minPrice)
    if maxPrice is not None:
        where.append("w.price <= ?")
        params.append(maxPrice)
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    sql = (
        "SELECT w.name as wname, w.description as wdesc, w.price as wprice, m.email as memail "
        "FROM wares w JOIN merchants m ON w.merchant_id = m.id"
        f"{where_clause} ORDER BY w.id DESC"
    )
    conn = connect_db()
    try:
        cur = conn.execute(sql, tuple(params))
        rows = cur.fetchall()
    finally:
        conn.close()

    # Build simple HTML table
    head = "<tr><th>Ware Name</th><th>Price</th><th>Description</th><th>Merchant Email</th></tr>"
    body_rows = []
    for r in rows:
        body_rows.append(
            "<tr>"
            f"<td>{escape(str(r['wname']))}</td>"
            f"<td>{escape(f'{r['wprice']:.2f}')}</td>"
            f"<td>{escape(str(r['wdesc']))}</td>"
            f"<td>{escape(str(r['memail']))}</td>"
            "</tr>"
        )
    if not body_rows:
        body_rows.append('<tr><td colspan="4">No wares found</td></tr>')
    table = f"<table border='1' cellpadding='6' cellspacing='0'>{head}{''.join(body_rows)}</table>"
    html = f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Wares</title>
</head>
<body>
<h1>Available Wares</h1>
{table}
</body>
</html>
""".strip()
    # Return with the media type specified in the schema
    return Response(content=html, media_type="application/html", status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)