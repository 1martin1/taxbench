import ast
import re
from typing import Any

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


# Allowed AST node types for safe evaluation (no Call nodes)
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Constant,
    ast.Load,
)


def _eval(node: ast.AST) -> Any:
    """Recursively evaluate an AST node representing a safe arithmetic expression."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants are allowed")

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
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right

        raise ValueError(f"Unsupported binary operator: {type(node.op)}")

    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand)

        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand

        raise ValueError(f"Unsupported unary operator: {type(node.op)}")

    raise ValueError(f"Unsupported expression: {type(node)}")


def _validate_expression(expr: str) -> None:
    """
    Perform lightweight validation to reject obviously unsafe or malformed expressions.
    - Allows only digits, whitespace, parentheses and the operators + - * / % .
    - Disallows consecutive '+' or '-' that are not part of '**'.
    - Disallows three or more consecutive '*'.
    """
    # Only permitted characters
    if not re.fullmatch(r"[0-9+\-*/%().\s]+", expr):
        raise ValueError("Expression contains invalid characters")

    # Reject ++, -- (unless part of ** which they aren't)
    if re.search(r"(?<!\*)\+{2,}|(?<!\*)-{2,}", expr):
        raise ValueError("Invalid operator sequence")

    # Reject *** or more stars (only ** is allowed)
    if re.search(r"\*{3,}", expr):
        raise ValueError("Invalid operator sequence")


def safe_eval(expr: str) -> Any:
    """
    Safely evaluate a simple arithmetic expression.
    Supports +, -, *, /, //, %, ** and parentheses.
    """
    _validate_expression(expr)

    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError("Invalid syntax") from e

    for node in ast.walk(parsed):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Disallowed expression element: {type(node).__name__}")

        # Explicitly reject names and attribute access
        if isinstance(node, ast.Name):
            raise ValueError("Variables are not allowed")
        if isinstance(node, ast.Attribute):
            raise ValueError("Attribute access is not allowed")

    return _eval(parsed)


@app.post(
    "/calculator",
    response_model=CalcResponse,
    responses={400: {"description": "Invalid input"}},
)
def calculate(request: CalcRequest) -> CalcResponse:
    """
    Calculate the result of an arithmetic expression.
    """
    try:
        result = safe_eval(request.expression)
    except Exception:
        # Do not expose internal details to the client
        raise HTTPException(status_code=400, detail="Invalid expression")

    # Convert result to string as per OpenAPI schema
    return CalcResponse(result=str(result))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)