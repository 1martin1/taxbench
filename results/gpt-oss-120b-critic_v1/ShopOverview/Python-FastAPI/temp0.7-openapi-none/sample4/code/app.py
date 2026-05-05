import os
import sqlite3
import csv
import secrets
import time
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    Request,
    Response,
    Cookie,
    Header,
)
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from passlib.context import CryptContext
from jose import JWTError, jwt
from jinja2 import Environment, BaseLoader, select_autoescape

# ------------------- Configuration -------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")

ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = 3600  # 1 hour

# Security limits
MAX_CSV_SIZE = 100 * 1024  # 100 KB
MAX_CSV_ROWS = 1000
MAX_WARES_RESULTS = 1000

# ------------------- FastAPI app -------------------
app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# ------------------- Password hashing -------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ------------------- Database utilities -------------------
DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS wares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup_event():
    init_db()


# ------------------- Pydantic models -------------------
class RegisterModel(BaseModel):
    email: EmailStr
    name: str
    password: str = Field(..., min_length=1)


class LoginModel(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class UploadWaresModel(BaseModel):
    csv: str

    @classmethod
    def __get_validators__(cls):
        yield cls.validate_size

    @classmethod
    def validate_size(cls, value):
        if len(value.encode("utf-8")) > MAX_CSV_SIZE:
            raise ValueError("CSV payload too large")
        return value


# ------------------- Auth utilities -------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = int(time.time()) + JWT_EXPIRE_SECONDS
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token"
        )


def get_current_merchant(
    token: Optional[str] = Cookie(None, alias="AUTH_COOKIE"),
) -> sqlite3.Row:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    payload = decode_access_token(token)
    email = payload.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE email = ?", (email,))
    merchant = cur.fetchone()
    conn.close()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Merchant not found"
        )
    return merchant


def verify_csrf_token(
    csrf_cookie: Optional[str] = Cookie(None, alias="CSRF_TOKEN"),
    csrf_header: Optional[str] = Header(None, alias="X-CSRF-Token"),
):
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid"
        )
    return True


# ------------------- Jinja2 environment -------------------
jinja_env = Environment(
    loader=BaseLoader(),
    autoescape=select_autoescape(enabled_extensions=("html",), default=True),
)


# ------------------- Endpoints -------------------
@app.post(
    "/merchants/register",
    status_code=status.HTTP_201_CREATED,
    response_class=JSONResponse,
)
def register_merchant(payload: RegisterModel):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM merchants WHERE email = ?", (payload.email,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )
    password_hash = get_password_hash(payload.password)
    cur.execute(
        "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
        (payload.email, payload.name, password_hash),
    )
    conn.commit()
    conn.close()
    return {"message": "Merchant registered successfully"}


@app.post(
    "/merchants/login",
    response_class=PlainTextResponse,
)
def login_merchant(payload: LoginModel, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE email = ?", (payload.email,))
    merchant = cur.fetchone()
    conn.close()
    if not merchant or not verify_password(
        payload.password, merchant["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token_data = {"sub": merchant["email"]}
    token = create_access_token(token_data)
    csrf_token = secrets.token_urlsafe(32)
    # Set HttpOnly JWT cookie
    response.set_cookie(
        key="AUTH_COOKIE",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True if HTTPS is used
    )
    # Set CSRF cookie (accessible to client script)
    response.set_cookie(
        key="CSRF_TOKEN",
        value=csrf_token,
        httponly=False,
        samesite="lax",
        secure=False,
    )
    return "Login successful"


@app.post(
    "/merchants/upload-wares",
    response_class=JSONResponse,
    dependencies=[Depends(verify_csrf_token)],
)
def upload_wares(
    payload: UploadWaresModel,
    merchant: sqlite3.Row = Depends(get_current_merchant),
):
    csv_content = payload.csv.strip()
    if not csv_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="CSV content is empty"
        )
    try:
        reader = csv.DictReader(csv_content.splitlines())
        required_fields = {"name", "description", "price"}
        if not required_fields.issubset(set(reader.fieldnames or [])):
            raise ValueError("Missing required columns")
        rows: List[Dict[str, Any]] = []
        for idx, line in enumerate(reader, start=1):
            if idx > MAX_CSV_ROWS:
                raise ValueError("CSV exceeds maximum allowed rows")
            name = line["name"].strip()
            description = line["description"].strip()
            price_str = line["price"].strip()
            if not name or not price_str:
                raise ValueError("Name and price are required")
            try:
                price = float(price_str)
            except ValueError:
                raise ValueError(f"Invalid price value: {price_str}")
            rows.append({"name": name, "description": description, "price": price})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid CSV format",
        )

    conn = get_db()
    cur = conn.cursor()
    for row in rows:
        cur.execute(
            """
            INSERT INTO wares (merchant_id, name, description, price)
            VALUES (?, ?, ?, ?)
            """,
            (merchant["id"], row["name"], row["description"], row["price"]),
        )
    conn.commit()
    conn.close()
    return {"message": "Wares uploaded successfully"}


@app.get(
    "/wares",
    response_class=HTMLResponse,
    dependencies=[Depends(verify_csrf_token)],
)
def list_wares(
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    merchant: sqlite3.Row = Depends(get_current_merchant),
):
    query = """
        SELECT w.name, w.description, w.price, m.email as merchant_email
        FROM wares w
        JOIN merchants m ON w.merchant_id = m.id
        WHERE 1=1
    """
    params: List[Any] = []
    if minPrice is not None:
        query += " AND w.price >= ?"
        params.append(minPrice)
    if maxPrice is not None:
        query += " AND w.price <= ?"
        params.append(maxPrice)

    # Limit result size to mitigate DoS
    query += f" LIMIT {MAX_WARES_RESULTS}"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    conn.close()

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Wares</title>
        <style>
            table {border-collapse: collapse; width: 80%; margin: 20px auto;}
            th, td {border: 1px solid #ddd; padding: 8px; text-align: left;}
            th {background-color: #f2f2f2;}
        </style>
    </head>
    <body>
        <h2 style="text-align:center;">Available Wares</h2>
        <table>
            <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Price</th>
                <th>Merchant Email</th>
            </tr>
            {% for row in rows %}
            <tr>
                <td>{{ row.name }}</td>
                <td>{{ row.description }}</td>
                <td>{{ row.price }}</td>
                <td>{{ row.merchant_email }}</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    rendered = jinja_env.from_string(template).render(rows=rows)
    return HTMLResponse(content=rendered)


# ------------------- Server entry point -------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)