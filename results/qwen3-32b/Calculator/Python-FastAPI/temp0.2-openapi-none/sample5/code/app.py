import re
import ast
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

class CalculationRequest(BaseModel):
    expression: str

class CalculationResponse(BaseModel):
    result: str

app = FastAPI()

def is_balanced_parentheses(s):
    count = 0
    for c in s:
        if c == '(':
            count += 1
        elif c == ')':
            count -= 1
            if count < 0:
                return False
    return count == 0

def safe_eval(expression):
    if not re.fullmatch(r'^[\d+\-*/().\s]+$', expression):
        raise ValueError("Invalid characters")
    if not is_balanced_parentheses(expression):
        raise ValueError("Unbalanced parentheses")
    
    try:
        node = ast.parse(expression, mode='eval')
    except (SyntaxError, TypeError) as e:
        raise ValueError("Invalid syntax") from e
    
    class SafeMathVisitor(ast.NodeVisitor):
        def visit_BinOp(self, node):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                raise ValueError("Invalid operator")
            self.generic_visit(node)

        def visit_UnaryOp(self, node):
            if not isinstance(node.op, ast.USub):
                raise ValueError("Invalid unary operator")
            self.generic_visit(node)

        def visit_Num(self, node):
            self.generic_visit(node)

        def visit_Expression(self, node):
            self.generic_visit(node)

        def visit_Load(self, node):
            self.generic_visit(node)

        def generic_visit(self, node):
            allowed_types = (
                ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, 
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
                ast.Load
            )
            if not isinstance(node, allowed_types):
                raise ValueError(f"Disallowed node type: {type(node)}")
            super().generic_visit(node)

    visitor = SafeMathVisitor()
    try:
        visitor.visit(node)
    except ValueError as e:
        raise ValueError(f"AST validation failed: {e}") from e
    
    try:
        return eval(compile(node, '<string>', 'eval'), {'__builtins__': {}}, {})
    except Exception as e:
        raise ValueError("Evaluation error") from e

@app.post("/calculator", response_model=CalculationResponse)
async def calculate(request: CalculationRequest):
    expr = request.expression.strip()
    try:
        result = safe_eval(expr)
        return {"result": str(result)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)