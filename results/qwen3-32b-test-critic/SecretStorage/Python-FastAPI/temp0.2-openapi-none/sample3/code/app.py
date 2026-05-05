```python
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from typing import Optional
import jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
import secrets

app = FastAPI()

# Configuration
SECRET_KEY = "your-secret-key-here"  # In production, use a secure random key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# In-memory database (for demo purposes)
users_db = {}

# Pydantic models
class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str

class UserInDB(BaseModel):
    email: EmailStr
    username: str
    hashed_password: str
    secret: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    message: str

class SecretRequest(BaseModel):
    username: str
    secret: str

# Helper functions
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    
    # Find user by username from token
    for user in users_db.values():
        if user.username == username:
            return user
    raise credentials_exception

# Endpoints
@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user: UserCreate):
    if user.email in users_db:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already in use"
        )
    
    hashed_password = get_password_hash(user.password)
    new_user = UserInDB(
        email=user.email,
        username=user.username,
        hashed_password=hashed_password
    )
    users_db[user.email] = new_user
    return {"message": "Registration successful"}

@app.post("/login")
async def login(email: str, password: str):
    user = users_db.get(email)
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        message="Login successful"
    )

@app.post("/set_secret")
async def set_secret(
    secret_data: SecretRequest,
    current_user: UserInDB = Depends(get_current_user)
):
    if current_user.username != secret_data.username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    
    current_user.secret = secret_data.secret
    return {"message": "Secret has been set successfully"}

@app.get("/get_secret")
async def get_secret(
    username: str,
    current_user: UserInDB = Depends(get_current_user)
):
    if current_user.username != username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    
    if not current_user.secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No secret found"
        )
    
    return {"secret": current_user.secret}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
```