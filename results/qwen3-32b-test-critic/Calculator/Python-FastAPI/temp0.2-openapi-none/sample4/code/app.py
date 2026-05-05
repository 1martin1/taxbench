from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

@app.post("/calculator")
async def calculate(calc_req: CalculationRequest):
    expr = calc_req.expression
    try:
        result = str(eval(expr, {'__builtins__': {}}, {}))
        return {"result": result}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)