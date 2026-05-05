import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import ast

app = FastAPI()

class CalculatorRequest(BaseModel):
    expression: str

class CalculatorResponse(BaseModel):
    result: str

@app.post("/calculator", response_model=CalculatorResponse)
async def calculator(request: CalculatorRequest):
    expr = request.expression

    # Prevent excessively long expressions
    if len(expr) > 100:
        raise HTTPException(status_code=400, detail="Expression too long")

    try:
        # Parse expression into AST
        node = ast.parse(expr, mode='eval')
    except SyntaxError:
        raise HTTPException(status_code=400, detail="Invalid syntax")

    # Allow only arithmetic operations and constants
    allowed_node_types = {
        ast.Expression, ast.BinOp, ast.UnaryOp, ast.USub, ast.UAdd,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
        ast.Constant
    }

    for child in ast.walk(node):
        if not isinstance(child, tuple(allowed_node_types)):
            raise HTTPException(status_code=400, detail="Invalid expression")

    try:
        # Safe evaluation of validated AST
        result = eval(compile(node, "<string>", "eval"))
    except Exception:
        raise HTTPException(status_code=400, detail="Evaluation error")

    return CalculatorResponse(result=str(result))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)