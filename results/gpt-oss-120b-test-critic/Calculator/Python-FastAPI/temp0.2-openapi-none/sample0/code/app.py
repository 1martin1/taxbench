import ast
import operator
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


# Pydantic model for request body
class CalcRequest(BaseModel):
    expression: str


# Pydantic model for response
class CalcResponse(BaseModel):
    result: str


# Mapping of ast operator nodes to actual functions
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node: ast.AST) -> Any:
    """
    Recursively evaluate an AST node representing a mathematical expression.
    Only a safe subset of operations is allowed.
    """
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Num):  # For Python <3.8 compatibility
        return node.n

    if isinstance(node, ast.Constant):  # Python 3.8+
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Invalid constant type")

    if isinstance(node, ast.BinOp):
        left = _eval(node.left)
        right = _eval(node.right)
        op_type = type(node.op)
        if op_type in OPERATORS:
            try:
                return OPERATORS[op_type](left, right)
            except ZeroDivisionError as e:
                raise ValueError("division by zero") from e
        raise ValueError(f"Unsupported binary operator: {op_type}")

    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand)
        op_type = type(node.op)
        if op_type in OPERATORS:
            return OPERATORS[op_type](operand)
        raise ValueError(f"Unsupported unary operator: {op_type}")

    # Disallow any other constructs (e.g., function calls, attribute access, etc.)
    raise ValueError(f"Unsupported expression: {type(node).__name__}")


def evaluate_expression(expr: str) -> float:
    """
    Safely evaluate a simple arithmetic expression and return the numeric result.
    """
    try:
        # Parse the expression into an AST node
        parsed = ast.parse(expr, mode="eval")

        # Ensure the parsed tree contains only allowed nodes
        allowed_nodes = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Num,
            ast.Constant,
            ast.Load,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.USub,
            ast.UAdd,
        )
        for subnode in ast.walk(parsed):
            if not isinstance(subnode, allowed_nodes):
                raise ValueError(f"Disallowed node type: {type(subnode).__name__}")

        # Evaluate safely
        result = _eval(parsed)
        return result
    except Exception as exc:
        raise ValueError(f"Invalid expression: {exc}") from exc


@app.post(
    "/calculator",
    response_model=CalcResponse,
    responses={400: {"description": "Invalid input"}},
)
def calculate(request: CalcRequest):
    """
    Calculate the result of an arithmetic expression.
    """
    expr = request.expression
    if not isinstance(expr, str) or not expr.strip():
        raise HTTPException(status_code=400, detail="Expression must be a non-empty string")
    try:
        result = evaluate_expression(expr)
        # Convert result to string, preserving integer formatting when appropriate
        if isinstance(result, float) and result.is_integer():
            result_str = str(int(result))
        else:
            result_str = str(result)
        return CalcResponse(result=result_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)