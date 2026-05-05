from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

@app.post("/calculator")
async def calculate(request: CalculationRequest):
    expr = request.expression.strip()
    try:
        # Evaluate expression in restricted environment
        result = eval(expr, {"__builtins__": {}}, {})
        return {"result": str(result)}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)