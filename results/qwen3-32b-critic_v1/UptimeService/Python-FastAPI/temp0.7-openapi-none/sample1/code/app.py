from fastapi import FastAPI, HTTPException, Request, status, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from datetime import datetime
import sqlite3
import os
from hmac import HMAC
from hashlib import sha256
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict

app = FastAPI()

# Rate limiting middleware
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 10, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.request_counts = defaultdict(list)

    async def dispatch(self, request, call_next):
        client_ip = request.client.host
        now = datetime.now()
        # Prune old timestamps
        self.request_counts[client_ip] = [t for t in self.request_counts[client_ip] if (now - t).total_seconds() < self.window_seconds]
        if len(self.request_counts[client_ip]) >= self.max_requests:
            return JSONResponse(status_code=429, content={"detail": "Too Many Requests"})
        self.request_counts[client_ip].append(now)
        response = await call_next(request)
        return response

app.add_middleware(RateLimitMiddleware, max_requests=10, window_seconds=60)

# Security utilities
def get_secret():
    return os.environ.get("APP_SECRET", "default_secret")

def hash_token(token: str, secret: str) -> str:
    hmac_obj = HMAC(secret.encode(), token.encode(), sha256)
    return hmac_obj.hexdigest()

# Database setup
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_status (
            service_id TEXT NOT NULL,
            token TEXT NOT NULL,
            last_notification TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (service_id, token)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

# Exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request payload"},
    )

# Pydantic models
class HeartbeatRequest(BaseModel):
    serviceId: str = Field(max_length=100)
    token: str = Field(max_length=100)

class ServicesRequest(BaseModel):
    token: str = Field(max_length=100)

class ServiceStatusResponse(BaseModel):
    serviceId: str
    lastNotification: str  # ISO 8601 format

# Endpoints
@app.post("/heartbeat")
async def register_heartbeat(heartbeat: HeartbeatRequest):
    service_id = heartbeat.serviceId
    token = heartbeat.token
    hashed_token = hash_token(token, get_secret())
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO service_status (service_id, token, last_notification)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (service_id, hashed_token))
        conn.commit()
        return {}
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

@app.post("/services")
async def get_services(request: ServicesRequest):
    token = request.token
    hashed_token = hash_token(token, get_secret())
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT service_id, last_notification
            FROM service_status
            WHERE token = ?
        ''', (hashed_token,))
        rows = cursor.fetchall()
        services = [{"serviceId": row[0], "lastNotification": row[1].replace(" ", "T")} for row in rows]
        return services
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)