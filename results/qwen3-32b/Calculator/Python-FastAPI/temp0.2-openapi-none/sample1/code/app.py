from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

class CalculationRequest(BaseModel):
    expression: str

@app.post("/calculator")
async def calculate(request: CalculationRequest):
    try:
        result = eval(request.expression, {"__builtins__": {}}, {})
        return {"result": str(result)}
    except:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)