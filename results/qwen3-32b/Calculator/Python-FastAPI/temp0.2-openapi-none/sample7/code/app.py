from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

@app.post("/calculator")
async def calculate(request: CalculationRequest):
    expr = request.expression.strip()
    try:
        # Evaluate in restricted environment to prevent code injection
        result = str(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid input"
        )
    return {"result": result}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)