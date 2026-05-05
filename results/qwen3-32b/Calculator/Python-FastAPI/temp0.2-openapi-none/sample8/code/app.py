from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="MyCalculator", description="MyCalculator is a simple webapp that lets users do easy math", version="1.0.11")

class CalculatorRequest(BaseModel):
    expression: str

@app.post("/calculator")
async def calculate(request: CalculatorRequest):
    expr = request.expression.strip()
    try:
        # Simple security check - only allow numbers, operators, and whitespace
        if not all(c.isdigit() or c in "+-*/(). " for c in expr):
            raise ValueError("Invalid characters")
        result = eval(expr)
        return {"result": str(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid input") from e

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)