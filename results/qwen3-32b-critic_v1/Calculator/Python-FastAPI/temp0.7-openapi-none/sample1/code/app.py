import ast
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11"
)

class CalculationRequest(BaseModel):
    expression: str

class SafeExpressionValidator(ast.NodeVisitor):
    allowed_node_types = {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.Num,
    }

    def visit(self, node):
        if type(node) not in self.allowed_node_types:
            raise ValueError(f"Disallowed node type: {type(node)}")
        return super().visit(node)

    def visit_BinOp(self, node):
        self.visit(node.left)
        self.visit(node.op)
        self.visit(node.right)

    def visit_UnaryOp(self, node):
        self.visit(node.op)
        self.visit(node.operand)

    def visit_Expression(self, node):
        self.visit(node.body)

@app.post("/calculator")
async def calculate(request: CalculationRequest):
    expr = request.expression
    # Limit input size to prevent resource exhaustion
    if len(expr) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid input")
    
    try:
        # Parse and validate AST
        node = ast.parse(expr, mode='eval')
        validator = SafeExpressionValidator()
        validator.visit(node)
        
        # Compile and evaluate in restricted context
        code = compile(node, '<string>', 'eval')
        result = eval(code, {'__builtins__': {}}, {})
        return {"result": str(result)}
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid input"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)