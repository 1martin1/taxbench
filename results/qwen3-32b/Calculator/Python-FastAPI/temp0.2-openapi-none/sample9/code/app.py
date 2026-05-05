from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

class CalculationResponse(BaseModel):
    result: str

@app.post("/calculator", response_model=CalculationResponse)
async def calculate(request: CalculationRequest):
    expr = request.expression.strip()
    try:
        # Basic safety check - only allow numbers and operators
        if not all(c.isdigit() or c in "+-*/(). " for c in expr):
            raise HTTPException(status_code=400, detail="Invalid input")
        result = eval(expr)
        return {"result": str(result)}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)