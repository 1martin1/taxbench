import re
import ast
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11"
)

class CalculatorRequest(BaseModel):
    expression: str

class CalculatorResponse(BaseModel):
    result: str

def check_ast(node):
    if isinstance(node, ast.Expression):
        return check_ast(node.body)
    elif isinstance(node, ast.BinOp):
        return (check_ast(node.left) and 
                check_ast(node.right) and 
                isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)))
    elif isinstance(node, ast.UnaryOp):
        return (check_ast(node.operand) and 
                isinstance(node.op, (ast.USub, ast.UAdd)))
    elif isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))
    elif isinstance(node, ast.Num):  # Backward compatibility
        return True
    else:
        return False

def evaluate_node(node):
    if isinstance(node, ast.Expression):
        return evaluate_node(node.body)
    elif isinstance(node, ast.BinOp):
        left = evaluate_node(node.left)
        right = evaluate_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        elif isinstance(node.op, ast.Sub):
            return left - right
        elif isinstance(node.op, ast.Mult):
            return left * right
        elif isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left / right
    elif isinstance(node, ast.UnaryOp):
        operand = evaluate_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        elif isinstance(node.op, ast.UAdd):
            return +operand
    elif isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Num):  # Backward compatibility
        return node.n
    else:
        raise ValueError(f"Invalid node type: {type(node)}")

def safe_eval(expression):
    try:
        node = ast.parse(expression, mode='eval')
    except SyntaxError as e:
        raise ValueError("Invalid syntax") from e
    if not check_ast(node):
        raise ValueError("Invalid expression")
    return evaluate_node(node)

@app.post("/calculator", response_model=CalculatorResponse)
async def calculate(request: CalculatorRequest):
    expression = request.expression.strip()
    
    # Input size check
    if len(expression) > 100:
        raise HTTPException(status_code=400, detail="Input too long")
    
    # Check for allowed characters (including decimals and operators)
    if not re.fullmatch(r'^[0-9+\-*/().\s.]+$', expression):
        raise HTTPException(status_code=400, detail="Invalid characters in expression")
    
    try:
        result = safe_eval(expression)
    except SyntaxError:
        raise HTTPException(status_code=400, detail="Invalid syntax")
    except ZeroDivisionError:
        raise HTTPException(status_code=400, detail="Division by zero")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=400, detail="An error occurred during evaluation")
    
    return {"result": str(result)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)