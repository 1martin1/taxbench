import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

@app.post("/calculator")
async def calculate(request: CalculationRequest):
    expr = request.expression
    try:
        compiled = compile(expr, "<string>", "eval")
        result = eval(compiled, {"__builtins__": {}}, {})
        # if not isinstance(result, (int, float)):
        #     raise ValueError("Result is not a number")
        return {"result": str(result)}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)