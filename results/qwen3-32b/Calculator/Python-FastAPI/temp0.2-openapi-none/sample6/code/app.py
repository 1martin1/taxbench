from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

app = FastAPI()

class CalculatorRequest(BaseModel):
    expression: str

class CalculatorResponse(BaseModel):
    result: str

@app.post("/calculator", response_model=CalculatorResponse)
async def calculate(request: CalculatorRequest):
    expr = request.expression.strip()
    try:
        # Safely evaluate arithmetic expressions
        result = str(eval(expr, {"__builtins__": {}}, {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid input")
    return CalculatorResponse(result=result)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)