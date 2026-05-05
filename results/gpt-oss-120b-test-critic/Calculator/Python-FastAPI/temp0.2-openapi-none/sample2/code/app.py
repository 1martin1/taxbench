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
    Visitor that safely evaluates arithmetic expressions.
    Only a whitelist of AST node types is allowed.
    A recursion depth limit prevents excessively nested expressions.
    """

    # Whitelisted node types
    _allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Num,          # Python < 3.8
        ast.Constant,    # Python >= 3.8
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.UAdd,
        ast.USub,
    )

    def __init__(self, max_depth: int = 20):
        self.max_depth = max_depth
        self._current_depth = 0

    def generic_visit(self, node: ast.AST) -> Any:
        # Enforce node whitelist
        if not isinstance(node, self._allowed_nodes):
            raise ValueError(f"Disallowed expression: {type(node).__name__}")

        # Enforce depth limit
        if self._current_depth >= self.max_depth:
            raise ValueError("Expression is too deeply nested")

        self._current_depth += 1
        result = super().generic_visit(node)
        self._current_depth -= 1
        return result

    # Evaluation methods -------------------------------------------------

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left = self.visit(node.left)
        right = self.visit(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right

        raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self.visit(node.operand)

        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand

        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

    def visit_Num(self, node: ast.Num) -> Any:  # pragma: no cover (Python <3.8)
        return node.n

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants are allowed")

    # Any other node types are automatically rejected by generic_visit


def evaluate_expression(expr: str) -> Any:
    """
    Validate and safely evaluate a simple arithmetic expression.
    Supports +, -, *, /, //, %, ** and parentheses.
    """
    # Basic size guard – prevents extremely long payloads
    if len(expr) > 200:
        raise ValueError("Expression is too long")

    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid syntax") from exc

    evaluator = SafeEvalVisitor(max_depth=20)
    return evaluator.visit(parsed)


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

    # Convert result to string as defined by the OpenAPI schema
    return JSONResponse(content={"result": str(result)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)