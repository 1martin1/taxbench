from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import uvicorn

app = FastAPI()

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Association(Base):
    __tablename__ = "associations"
    credit_card = Column(String, primary_key=True)
    phone = Column(String, primary_key=True)

Base.metadata.create_all(bind=engine)

# Pydantic models
class AssociateCardRequest(BaseModel):
    credit_card: str
    phone: str

class RetrieveCardsRequest(BaseModel):
    phone_numbers: list[str]

# Exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return Response(status_code=400, content="Invalid request")

# Endpoints
@app.post("/associate_card", status_code=201)
async def associate_card(request: AssociateCardRequest):
    db = SessionLocal()
    try:
        existing = db.query(Association).filter_by(credit_card=request.credit_card, phone=request.phone).first()
        if existing:
            raise HTTPException(status_code=400, detail="Association already exists")
        new_assoc = Association(credit_card=request.credit_card, phone=request.phone)
        db.add(new_assoc)
        db.commit()
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()
    return Response(status_code=201)

@app.post("/retrieve_cards")
async def retrieve_cards(request: RetrieveCardsRequest):
    db = SessionLocal()
    phone_numbers = request.phone_numbers
    if not phone_numbers:
        raise HTTPException(status_code=400, detail="Empty phone numbers array")
    count = len(phone_numbers)
    try:
        results = (
            db.query(Association.credit_card)
            .filter(Association.phone.in_(phone_numbers))
            .group_by(Association.credit_card)
            .having(func.count(func.distinct(Association.phone)) == count)
            .all()
        )
        card_numbers = [r[0] for r in results]
        if not card_numbers:
            raise HTTPException(status_code=404, detail="No cards found")
        return {"card_numbers": card_numbers}
    finally:
        db.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)