import ast
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import uvicorn

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class ExpressionRequest(BaseModel):
    expression: str


class ResultResponse(BaseModel):
    result: str


# Allowed AST node types for safe evaluation
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


def _eval_node(node: ast.AST) -> float:
    """Recursively evaluate an AST node that has been validated."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("Only numeric constants are allowed")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)

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
        raise ValueError(f"Unsupported binary operator {type(node.op)}")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator {type(node.op)}")

    raise ValueError(f"Unsupported AST node {type(node)}")


def safe_eval(expression: str) -> float:
    """
    Safely evaluate a simple arithmetic expression.
    Supports +, -, *, /, //, %, ** and parentheses.
    """
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid syntax") from exc

    # Verify that only allowed nodes are present
    for node in ast.walk(parsed):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Disallowed expression element: {type(node).__name__}")

        # Explicitly forbid function calls, variable names, attribute access, etc.
        if isinstance(node, ast.Call):
            raise ValueError("Function calls are not allowed")
        if isinstance(node, ast.Name):
            raise ValueError("Variables are not allowed")
        if isinstance(node, ast.Subscript):
            raise ValueError("Subscript notation is not allowed")
        if isinstance(node, ast.Attribute):
            raise ValueError("Attribute access is not allowed")

    return _eval_node(parsed)


@app.post(
    "/calculator",
    response_model=ResultResponse,
    responses={400: {"description": "Invalid input"}},
)
def calculate(request: ExpressionRequest):
    """
    Calculate the result of an arithmetic expression.
    """
    expr = request.expression
    if not isinstance(expr, str) or not expr.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expression must be a non-empty string",
        )

    try:
        result = safe_eval(expr)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Convert result to string, removing trailing .0 for integer values
    result_str = str(int(result)) if isinstance(result, float) and result.is_integer() else str(result)
    return ResultResponse(result=result_str)


if __name__ == "__main__":
    # Run on 0.0.0.0:5000 as requested
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)