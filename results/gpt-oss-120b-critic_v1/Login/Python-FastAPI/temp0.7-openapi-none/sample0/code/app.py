import os
import sqlite3
from datetime import datetime, timedelta
from typing import Generator, List, Dict

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, validator
from passlib.context import CryptContext
import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# -------------------- Configuration --------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("Environment variable APP_SECRET must be set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 1

MAX_CONTENT_LENGTH = 10 * 1024  # 10 KiB
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 10  # per IP per window

# -------------------- Security --------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, APP_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        ) from e


async def optional_auth(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Global dependency that validates JWT if it is present.
    Does not enforce authentication for endpoints that are meant to be public.
    """
    if credentials:
        decode_access_token(credentials.credentials)
    return None


# -------------------- Rate Limiting --------------------
_attempt_tracker: Dict[str, List[float]] = {}


def check_rate_limit(request: Request):
    client_ip = request.client.host
    now = datetime.utcnow().timestamp()
    timestamps = _attempt_tracker.get(client_ip, [])
    # Remove timestamps outside the window
    timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later",
        )
    timestamps.append(now)
    _attempt_tracker[client_ip] = timestamps


# -------------------- Middleware --------------------
class ContentLengthLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    size = int(content_length)
                    if size > MAX_CONTENT_LENGTH:
                        return JSONResponse(
                            {"message": "Request payload too large"},
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        )
                except ValueError:
                    pass  # ignore malformed header, let downstream handle
        response = await call_next(request)
        return response


# -------------------- Database --------------------
DB_PATH = "db.sqlite3"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_user_by_email(db: sqlite3.Connection, email: str):
    cur = db.execute("SELECT * FROM users WHERE email = ?", (email,))
    return cur.fetchone()


def create_user(db: sqlite3.Connection, email: str, password_hash: str, name: str):
    try:
        db.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (email, password_hash, name),
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        ) from e


# -------------------- Pydantic Models --------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(..., min_length=1, max_length=100)

    @validator("password")
    def password_complexity(cls, v):
        # Basic complexity: at least one number and one letter
        if not any(c.isdigit() for c in v) or not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter and one number")
        return v


class RegisterResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginResponse(BaseModel):
    token: str
    message: str


class ErrorResponse(BaseModel):
    message: str


# -------------------- FastAPI App --------------------
app = FastAPI(
    title="LoginAPI",
    description="A simple webapp API for login and registration functionality.",
    version="1.0.11",
    dependencies=[Depends(optional_auth)],
    openapi_tags=[{"name": "Authentication", "description": "Operations related to user authentication"}],
)

# Add security scheme to OpenAPI (required for global security definition)
app.add_middleware(ContentLengthLimitMiddleware)


@app.on_event("startup")
def on_startup():
    init_db()


# -------------------- Endpoints --------------------
@app.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    tags=["Authentication"],
    dependencies=[Depends(check_rate_limit)],
)
def register(
    payload: RegisterRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    if get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use or invalid data",
        )
    password_hash = get_password_hash(payload.password)
    create_user(db, payload.email, password_hash, payload.name)
    return RegisterResponse(message="Registration successful")


@app.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    tags=["Authentication"],
    dependencies=[Depends(check_rate_limit)],
)
def login(
    payload: LoginRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    user = get_user_by_email(db, payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_access_token({"sub": payload.email})
    return LoginResponse(token=token, message="Login successful")


# -------------------- Run Server --------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)