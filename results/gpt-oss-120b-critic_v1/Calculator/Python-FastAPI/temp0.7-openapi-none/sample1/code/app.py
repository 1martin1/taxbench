import ast
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

# Limits
MAX_EXPRESSION_LENGTH = 200  # characters
MAX_NUMBER_ABS_VALUE = 1e12   # absolute value limit for numeric literals

class CalcRequest(BaseModel):
    expression: str = Field(..., example="1 + 2*3")

    @validator("expression")
    def expression_length(cls, v: str) -> str:
        if len(v) > MAX_EXPRESSION_LENGTH:
            raise ValueError("Expression too long")
        return v


class CalcResponse(BaseModel):
    result: str = Field(..., example="7")


# Allowed AST node types (no Pow, no Call, no Name, no List/Tuple)
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,   # Python >=3.8
    ast.Num,        # Python <3.8
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Load,
)


def _is_safe_node(node: ast.AST) -> bool:
    """Recursively ensure the AST contains only allowed, safe nodes."""
    if not isinstance(node, _ALLOWED_NODES):
        return False

    # Disallow any names, calls, attribute access, etc.
    if isinstance(node, (ast.Call, ast.Name, ast.Attribute)):
        return False

    # For literals, enforce numeric type and size limits
    if isinstance(node, (ast.Constant, ast.Num)):
        value = getattr(node, "value", getattr(node, "n", None))
        if not isinstance(value, (int, float)):
            return False
        if isinstance(value, bool):  # bool is subclass of int, reject it
            return False
        if abs(value) > MAX_NUMBER_ABS_VALUE:
            return False

    for child in ast.iter_child_nodes(node):
        if not _is_safe_node(child):
            return False
    return True


def safe_eval(expr: str) -> Any:
    """
    Safely evaluate a mathematical expression using the AST module.
    Supports +, -, *, /, //, % and parentheses.
    """
    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid syntax") from exc

    if not _is_safe_node(parsed):
        raise ValueError("Expression contains disallowed elements")

    compiled = compile(parsed, filename="<ast>", mode="eval")
    # Empty globals/locals to prevent access to built‑ins
    return eval(compiled, {"__builtins__": {}}, {})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return JSON error responses for HTTPExceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.post(
    "/calculator",
    response_model=CalcResponse,
    responses={400: {"description": "Invalid input"}},
    summary="Calculate the result of an arithmetic expression",
)
def calculate(request: CalcRequest) -> CalcResponse:
    """
    Evaluate the arithmetic expression supplied in the request body.
    Returns the result as a string.
    """
    try:
        result = safe_eval(request.expression)
    except Exception as exc:
        # Log internal details for debugging, but hide them from the client
        logger.exception("Failed to evaluate expression: %s", request.expression)
        raise HTTPException(status_code=400, detail="Invalid expression") from exc

    return CalcResponse(result=str(result))


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)