import ast
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class SafeEvalVisitor(ast.NodeVisitor):
    """
    Visitor that ensures the parsed AST contains only safe nodes for arithmetic evaluation.
    """

    allowed_nodes = {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Num,          # Python <3.8
        ast.Constant,     # Python >=3.8
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Load,
        ast.Expr,
        ast.Paren,
    }

    def generic_visit(self, node: ast.AST) -> Any:
        if type(node) not in self.allowed_nodes:
            raise ValueError(f"Disallowed expression: {type(node).__name__}")
        super().generic_visit(node)


def evaluate_expression(expr: str) -> Any:
    """
    Safely evaluate a simple arithmetic expression and return the result.
    Supports +, -, *, /, //, %, ** and parentheses.
    """
    try:
        # Parse the expression into an AST node
        parsed = ast.parse(expr, mode="eval")
        # Verify that the AST contains only safe nodes
        SafeEvalVisitor().visit(parsed)
        # Compile and evaluate the expression in a restricted namespace
        compiled = compile(parsed, filename="<ast>", mode="eval")
        return eval(compiled, {"__builtins__": {}}, {})
    except Exception as exc:
        raise ValueError(f"Invalid expression: {exc}") from exc


@app.post("/calculator")
async def calculate(request: Request):
    """
    Calculate the result of an arithmetic expression.
    Expects JSON body: {"expression": "1 + 2*3"}
    Returns JSON: {"result": "7"}
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(payload, dict) or "expression" not in payload:
        raise HTTPException(status_code=400, detail="Missing 'expression' field")

    expression = payload["expression"]
    if not isinstance(expression, str):
        raise HTTPException(status_code=400, detail="'expression' must be a string")

    try:
        result = evaluate_expression(expression)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # Convert result to string as per OpenAPI schema
    return JSONResponse(content={"result": str(result)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)