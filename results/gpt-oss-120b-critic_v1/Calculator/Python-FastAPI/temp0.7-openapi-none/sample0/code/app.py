from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator
import ast

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

# Configuration constants
MAX_EXPRESSION_LENGTH = 100  # characters
MAX_AST_DEPTH = 30  # recursion depth limit for expression evaluation


class CalcRequest(BaseModel):
    expression: str = Field(..., max_length=MAX_EXPRESSION_LENGTH)

    @validator("expression")
    def no_newlines(cls, v: str) -> str:
        """Disallow newline characters which could be used to hide malicious payloads."""
        if "\n" in v or "\r" in v:
            raise ValueError("Expression must be a single line")
        return v


class CalcResponse(BaseModel):
    result: str


def _eval_node(node: ast.AST, depth: int = 0):
    """Recursively evaluate a safe subset of AST nodes with depth limiting."""
    if depth > MAX_AST_DEPTH:
        raise ValueError("Expression is too deeply nested")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)

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

        # Exponentiation is deliberately omitted for safety
        raise ValueError("Unsupported binary operator")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, depth + 1)

        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand

        raise ValueError("Unsupported unary operator")

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Unsupported constant type")

    # Compatibility for Python <3.8 (ast.Num)
    if isinstance(node, ast.Num):
        if isinstance(node.n, (int, float)):
            return node.n
        raise ValueError("Unsupported number type")

    raise ValueError("Unsupported expression element")


def safe_eval(expression: str):
    """Parse and safely evaluate a mathematical expression."""
    # Enforce length limit early (also enforced by Pydantic, but double‑check)
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("Expression exceeds maximum allowed length")

    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid syntax") from exc

    # Ensure the parsed AST contains only a single expression node
    if not isinstance(parsed, ast.Expression):
        raise ValueError("Only a single expression is allowed")

    return _eval_node(parsed.body)


@app.post(
    "/calculator",
    response_model=CalcResponse,
    summary="Calculate the result of an arithmetic expression",
)
def calculate(request: CalcRequest):
    try:
        result = safe_eval(request.expression)
    except Exception as exc:
        # Return a generic 400 error without leaking internal details
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CalcResponse(result=str(result))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)