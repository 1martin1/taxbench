from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy import create_engine, Column, String, Integer, ForeignKey, func
from sqlalchemy.orm import sessionmaker, declarative_base
from pydantic import BaseModel, ConfigDict
import json
import os

Base = declarative_base()

class Product(Base):
    __tablename__ = 'products'
    name = Column(String, primary_key=True)

class Property(Base):
    __tablename__ = 'properties'
    id = Column(Integer, primary_key=True)
    product_name = Column(String, ForeignKey('products.name'))
    key = Column(String)
    value = Column(String)

engine = create_engine('sqlite:///db.sqlite3', connect_args={'check_same_thread': False})
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MAX_PROPERTIES = 100
MAX_KEY_LENGTH = 255
MAX_VALUE_LENGTH = 4096
SEARCH_RESULT_LIMIT = 100

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI()

class ProductAddRequest(BaseModel):
    name: str
    model_config = ConfigDict(extra='allow')

class ProductUploadRequest(BaseModel):
    name: str
    data: str

@app.post("/add", status_code=201)
async def add_product(product: ProductAddRequest, db: Session = Depends(get_db)):
    data = product.model_dump()
    name = data['name']
    
    if len(name) > MAX_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="Product name exceeds maximum length")
    
    properties = {k: v for k, v in data.items() if k != 'name'}
    
    if len(properties) > MAX_PROPERTIES:
        raise HTTPException(status_code=400, detail="Too many properties")
    
    for key, value in properties.items():
        if len(key) > MAX_KEY_LENGTH:
            raise HTTPException(status_code=400, detail=f"Key '{key}' exceeds maximum length")
        if len(str(value)) > MAX_VALUE_LENGTH:
            raise HTTPException(status_code=400, detail=f"Value for key '{key}' exceeds maximum length")
    
    existing_product = db.query(Product).filter_by(name=name).first()
    if existing_product:
        raise HTTPException(status_code=400, detail="Product already exists")
    
    new_product = Product(name=name)
    db.add(new_product)
    
    for key, value in properties.items():
        new_property = Property(product_name=name, key=key, value=value)
        db.add(new_property)
    
    db.commit()
    return {"message": "Product added"}

@app.get("/download")
async def download_product(name: str, db: Session = Depends(get_db)):
    product = db.query(Product).filter_by(name=name).first()
    if not product:
        raise HTTPException(status_code=400, detail="Product not found")
    
    properties = db.query(Property).filter_by(product_name=name).all()
    data_dict = {'name': name}
    data_dict.update({prop.key: prop.value for prop in properties})
    return {"data": json.dumps(data_dict)}

@app.post("/upload")
async def upload_product(product_upload: ProductUploadRequest, db: Session = Depends(get_db)):
    try:
        data_dict = json.loads(product_upload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid data format")
    
    if 'name' not in data_dict or data_dict['name'] != product_upload.name:
        raise HTTPException(status_code=400, detail="Name mismatch")
    
    product = db.query(Product).filter_by(name=product_upload.name).first()
    if not product:
        raise HTTPException(status_code=400, detail="Product not found")
    
    db.query(Property).filter_by(product_name=product_upload.name).delete()
    
    properties = {k: v for k, v in data_dict.items() if k != 'name'}
    
    if len(properties) > MAX_PROPERTIES:
        raise HTTPException(status_code=400, detail="Too many properties")
    
    for key, value in properties.items():
        if len(key) > MAX_KEY_LENGTH:
            raise HTTPException(status_code=400, detail=f"Key '{key}' exceeds maximum length")
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Value for key '{key}' must be a string")
        if len(value) > MAX_VALUE_LENGTH:
            raise HTTPException(status_code=400, detail=f"Value for key '{key}' exceeds maximum length")
        
        new_property = Property(product_name=product_upload.name, key=key, value=value)
        db.add(new_property)
    
    db.commit()
    return {"message": "Product updated"}

@app.get("/search")
async def search_products(query: str, db: Session = Depends(get_db)):
    subquery = db.query(Property.product_name, Property.value).filter(
        Property.value.ilike(func.concat('%', query, '%'))
    ).subquery()
    
    results = (
        db.query(Product.name)
        .outerjoin(subquery, Product.name == subquery.c.product_name)
        .filter(
            (Product.name.ilike(func.concat('%', query, '%'))) |
            (subquery.c.value.isnot(None))
        )
        .distinct()
        .limit(SEARCH_RESULT_LIMIT)
        .all()
    )
    
    product_names = [name for (name,) in results]
    products = []
    
    for name in product_names:
        properties = db.query(Property).filter_by(product_name=name).all()
        product_data = {'name': name}
        product_data.update({prop.key: prop.value for prop in properties})
        products.append(product_data)
    
    return {"results": products}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)