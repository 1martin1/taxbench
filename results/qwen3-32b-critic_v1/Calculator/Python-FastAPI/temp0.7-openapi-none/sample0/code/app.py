from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import re
import ast
import asyncio

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

class CalculationResponse(BaseModel):
    result: str

def evaluate_ast(node):
    if isinstance(node, ast.Expression):
        return evaluate_ast(node.body)
    elif isinstance(node, ast.BinOp):
        left = evaluate_ast(node.left)
        right = evaluate_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        elif isinstance(node.op, ast.Sub):
            return left - right
        elif isinstance(node.op, ast.Mult):
            return left * right
        elif isinstance(node.op, ast.Div):
            return left / right
        else:
            raise TypeError("Unsupported operation")
    elif isinstance(node, ast.UnaryOp):
        operand = evaluate_ast(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        else:
            raise TypeError("Unsupported unary operation")
    elif isinstance(node, (ast.Num, ast.Constant)):
        value = node.n if isinstance(node, ast.Num) else node.value
        if not isinstance(value, (int, float)):
            raise TypeError("Invalid constant value")
        return value
    else:
        raise TypeError(f"Invalid AST node: {type(node)}")

@app.post("/calculator", response_model=CalculationResponse)
async def calculate(request: CalculationRequest):
    expression = request.expression.strip()
    
    # Input validation
    if len(expression) > 500:
        raise HTTPException(status_code=400, detail="Invalid input")
    if not re.fullmatch(r"^[0-9+\-*/().\s]+$", expression):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    try:
        # Parse the expression into an AST
        node = ast.parse(expression, mode='eval')
    except (SyntaxError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid input")
    
    try:
        # Evaluate the AST with timeout
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, evaluate_ast, node),
            timeout=5  # 5 seconds timeout to prevent CPU exhaustion
        )
        return {"result": str(result)}
    except (TypeError, ZeroDivisionError, asyncio.TimeoutError, SyntaxError, KeyError):
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)