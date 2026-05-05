from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import re
import ast
import asyncio

app = FastAPI()

class CalculationRequest(BaseModel):
    expression: str

def validate_ast(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Expression):
            continue
        elif isinstance(n, (ast.Num, ast.Constant)):
            if not isinstance(n.value, (int, float)):
                return False
        elif isinstance(n, ast.BinOp):
            if not isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                return False
        elif isinstance(n, ast.UnaryOp):
            if not isinstance(n.op, ast.USub):
                return False
        elif isinstance(n, (ast.operator, ast.cmpop, ast.boolop)):
            continue
        else:
            return False
    return True

@app.post("/calculator")
async def calculate(request: CalculationRequest):
    expr = request.expression.strip()
    
    # Input length limit (1000 characters)
    if len(expr) > 1000:
        raise HTTPException(status_code=400, detail="Input too long")
    
    # Character validation for basic arithmetic
    if not re.fullmatch(r'^[\d+\-*/().\s]+$', expr):
        raise HTTPException(status_code=400, detail="Invalid characters in expression")
    
    # AST validation for allowed operations
    try:
        node = ast.parse(expr, mode='eval')
    except SyntaxError:
        raise HTTPException(status_code=400, detail="Invalid syntax")
    
    if not validate_ast(node):
        raise HTTPException(status_code=400, detail="Invalid operations in expression")
    
    # Compile AST node
    compiled_expr = compile(node, "<string>", "eval")
    
    # Evaluate with timeout and sandboxed environment
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: eval(compiled_expr, {"__builtins__": {}}, {})
            ),
            timeout=1.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=400, detail="Calculation timed out")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")
    
    return {"result": str(result)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)