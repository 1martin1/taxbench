import os
import sqlite3
import csv
import io
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Response,
    status,
    Cookie,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, EmailStr, Field
from passlib.context import CryptContext
import jwt
from jwt import PyJWTError
from jinja2 import Environment, select_autoescape

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set")
DATABASE = "db.sqlite3"
AUTH_COOKIE_NAME = "AUTH_COOKIE"
TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day
MAX_CSV_SIZE = 100_000  # 100 KB
MAX_FIELD_LENGTH = 255

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Merchant WebApp API", version="1.0.0")

# -------------------- Database Utilities --------------------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
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
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            merchant_id INTEGER NOT NULL,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()

init_db()

# -------------------- Pydantic Models --------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=MAX_FIELD_LENGTH)
    password: str = Field(..., min_length=1)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UploadWaresRequest(BaseModel):
    csv: str

# -------------------- Auth Helpers --------------------
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm="HS256")

def verify_token(token: str) -> int:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        merchant_id: int = payload.get("sub")
        if merchant_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return merchant_id
    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

async def get_current_merchant(
    auth_cookie: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME)
) -> sqlite3.Row:
    if not auth_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    merchant_id = verify_token(auth_cookie)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,))
    merchant = cur.fetchone()
    conn.close()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Merchant not found",
        )
    return merchant

# -------------------- Exception Handlers --------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 to 400 as per OpenAPI spec
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Invalid input"},
    )

# -------------------- Endpoints --------------------
@app.post("/merchants/register", status_code=status.HTTP_201_CREATED)
def register_merchant(req: RegisterRequest):
    conn = get_db()
    cur = conn.cursor()
    password_hash = pwd_context.hash(req.password)
    try:
        cur.execute(
            "INSERT INTO merchants (email, name, password_hash) VALUES (?, ?, ?)",
            (req.email, req.name, password_hash),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    conn.close()
    return {"message": "Merchant registered successfully"}

@app.post("/merchants/login")
def login_merchant(req: LoginRequest, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM merchants WHERE email = ?", (req.email,))
    merchant = cur.fetchone()
    conn.close()
    if not merchant or not pwd_context.verify(req.password, merchant["password_hash"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "Invalid email or password"},
        )
    token = create_access_token({"sub": merchant["id"]})
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=TOKEN_EXPIRE_MINUTES * 60,
    )
    # Return raw JSON string as per spec
    return "Login successful"

@app.post("/merchants/upload-wares")
def upload_wares(
    req: UploadWaresRequest,
    merchant: sqlite3.Row = Depends(get_current_merchant),
):
    csv_content = req.csv.strip()
    if not csv_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV content empty",
        )
    if len(csv_content.encode("utf-8")) > MAX_CSV_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV content too large",
        )
    f = io.StringIO(csv_content)
    reader = csv.reader(f)
    # Process header detection without loading all rows
    try:
        first_row = next(reader)
    except StopIteration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV has no rows",
        )
    header = [h.strip().lower() for h in first_row]
    has_header = set(header) >= {"name", "description", "price"}
    rows_iter = reader if has_header else ([first_row] + list(reader))

    wares_to_insert: List[tuple] = []
    for idx, row in enumerate(rows_iter, start=2 if has_header else 1):
        if len(row) != 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Row {idx} does not have exactly 3 columns",
            )
        name, description, price_str = (cell.strip() for cell in row)
        if len(name) > MAX_FIELD_LENGTH or len(description) > MAX_FIELD_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Row {idx} contains field exceeding allowed length",
            )
        try:
            price = float(price_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid price on row {idx}",
            )
        wares_to_insert.append((name, description, price, merchant["id"]))

    if not wares_to_insert:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid wares to insert",
        )

    conn = get_db()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO wares (name, description, price, merchant_id) VALUES (?, ?, ?, ?)",
        wares_to_insert,
    )
    conn.commit()
    conn.close()
    return {"message": "Wares uploaded successfully"}

@app.get("/wares", response_class=HTMLResponse)
def list_wares(
    minPrice: Optional[float] = Query(default=None),
    maxPrice: Optional[float] = Query(default=None),
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

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    conn.close()

    # Convert rows to dicts for template rendering
    wares = [dict(row) for row in rows]

    template_str = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Wares</title>
        <style>
            table { border-collapse: collapse; width: 80%; margin: 20px auto; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h2 style="text-align:center;">Offered Wares</h2>
        <table>
            <tr>
                <th>Name</th>
                <th>Price</th>
                <th>Description</th>
                <th>Merchant Email</th>
            </tr>
            {% for ware in wares %}
            <tr>
                <td>{{ ware.name }}</td>
                <td>{{ "%.2f"|format(ware.price) }}</td>
                <td>{{ ware.description }}</td>
                <td>{{ ware.merchant_email }}</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    env = Environment(autoescape=select_autoescape(['html', 'xml']))
    template = env.from_string(template_str)
    html_content = template.render(wares=wares)
    return HTMLResponse(content=html_content)

# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)