```python
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List

app = FastAPI()

# In-memory storage for products
products = []

class ProductIn(BaseModel):
    product_name: str
    tags: List[str]

@app.post("/recommender")
async def post_product(product: ProductIn):
    if not product.product_name or not product.tags:
        raise HTTPException(status_code=400, detail="Invalid input")
    products.append(product)
    return {"status": "success"}

@app.get("/recommender", response_class=str)
async def get_products(tags: str = Query(..., description="Tags to search for separated by comma")):
    tag_list = [tag.strip() for tag in tags.split(",")]
    if not tag_list:
        raise HTTPException(status_code=400, detail="Tags list cannot be empty")
    
    matched_products = []
    for product in products:
        if any(tag in product.tags for tag in tag_list):
            matched_products.append(product.product_name)
    
    html_content = "<ul>" + "".join(f"<li>{product}</li>" for product in matched_products) + "</ul>"
    return html_content

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
```