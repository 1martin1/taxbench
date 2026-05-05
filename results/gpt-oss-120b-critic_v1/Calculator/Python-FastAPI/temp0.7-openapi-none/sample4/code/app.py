import ast
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class CalcRequest(BaseModel):
    expression: str = Field(..., example="1 + 2*3")


class CalcResponse(BaseModel):
    result: str = Field(..., example="7")


# Allowed AST node types
ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Num,          # for Python <3.8
    ast.Constant,     # for numbers in Python >=3.8
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.UAdd,
    ast.USub,
    ast.Load,
    ast.Expr,
)

# Limits to mitigate resource‑exhaustion attacks
MAX_EXPRESSION_LENGTH = 200          # characters
MAX_AST_NODES = 100                  # total nodes
MAX_AST_DEPTH = 20                   # nesting depth


def _node_depth(node: ast.AST, current: int = 0) -> int:
    """Return the maximum depth of an AST."""
    if not list(ast.iter_child_nodes(node)):
        return current
    return max(_node_depth(child, current + 1) for child in ast.iter_child_nodes(node))


def _eval(node: ast.AST) -> float:
    """Recursively evaluate an already‑validated AST node."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.BinOp):
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        raise ValueError("Unsupported binary operator")
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("Unsupported unary operator")
    if isinstance(node, ast.Num):  # pragma: no cover (Python <3.8)
        return node.n
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants are allowed")
    raise ValueError("Unsupported AST node")


def safe_eval(expr: str) -> float:
    """
    Safely evaluate a mathematical expression containing only numbers and
    basic arithmetic operators. Raises ValueError on any problem.
    """
    # Basic length guard
    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise ValueError("Expression too long")

    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ValueError("Invalid syntax")

    # Guard against overly complex expressions
    node_count = sum(1 for _ in ast.walk(parsed))
    if node_count > MAX_AST_NODES:
        raise ValueError("Expression too complex")
    if _node_depth(parsed) > MAX_AST_DEPTH:
        raise ValueError("Expression nesting too deep")

    for node in ast.walk(parsed):
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError("Disallowed expression element")

    return _eval(parsed)


@app.post(
    "/calculator",
    response_model=CalcResponse,
    responses={400: {"description": "Invalid input"}},
    summary="Calculate the result of an arithmetic expression",
)
async def calculate(request: CalcRequest):
    try:
        result_value = safe_eval(request.expression)
    except ValueError:
        # Do not expose internal details
        raise HTTPException(status_code=400, detail="Invalid expression")

    # Convert result to string to match the OpenAPI example
    return CalcResponse(result=str(result_value))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)