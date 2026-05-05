from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Optional

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

MAX_EXPRESSION_LENGTH = 256
MAX_AST_NODES = 128
MAX_AST_DEPTH = 32
MAX_NUMERIC_LITERAL_LENGTH = 64
MAX_RESULT_STRING_LENGTH = 256


class CalculatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expression: Optional[str] = Field(default=None, examples=["1 + 2*3"])


class CalculatorResponse(BaseModel):
    result: str = Field(..., examples=["7"])


def _invalid_input_response() -> JSONResponse:
    return JSONResponse(status_code=400, content=None)


def _decimal_to_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        text = format(normalized.quantize(Decimal("1")), "f")
    else:
        text = format(normalized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")

    if len(text) > MAX_RESULT_STRING_LENGTH:
        raise ValueError("Invalid input")
    return text


def _parse_decimal_literal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Invalid input")

    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError("Invalid input")
        text = str(int(value))
    else:
        raise ValueError("Invalid input")

    if len(text.lstrip("-")) > MAX_NUMERIC_LITERAL_LENGTH:
        raise ValueError("Invalid input")

    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid input")


def _validate_ast(node: ast.AST, depth: int = 0) -> tuple[int, int]:
    if depth > MAX_AST_DEPTH:
        raise ValueError("Invalid input")

    if isinstance(node, ast.Expression):
        child_count, child_depth = _validate_ast(node.body, depth + 1)
        return child_count + 1, max(depth, child_depth)

    if isinstance(node, ast.Constant):
        _parse_decimal_literal(node.value)
        return 1, depth

    if isinstance(node, ast.Num):  # pragma: no cover
        _parse_decimal_literal(node.n)
        return 1, depth

    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            raise ValueError("Invalid input")
        left_count, left_depth = _validate_ast(node.left, depth + 1)
        right_count, right_depth = _validate_ast(node.right, depth + 1)
        return left_count + right_count + 1, max(depth, left_depth, right_depth)

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise ValueError("Invalid input")
        operand_count, operand_depth = _validate_ast(node.operand, depth + 1)
        return operand_count + 1, max(depth, operand_depth)

    raise ValueError("Invalid input")


def _evaluate_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)

    if isinstance(node, ast.Constant):
        return _parse_decimal_literal(node.value)

    if isinstance(node, ast.Num):  # pragma: no cover
        return _parse_decimal_literal(node.n)

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right

        raise ValueError("Invalid input")

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("Invalid input")

    raise ValueError("Invalid input")


def evaluate_expression(expression: Optional[str]) -> str:
    if expression is None or not expression.strip():
        raise ValueError("Invalid input")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("Invalid input")

    try:
        parsed = ast.parse(expression, mode="eval")
        node_count, _ = _validate_ast(parsed)
        if node_count > MAX_AST_NODES:
            raise ValueError("Invalid input")
        result = _evaluate_node(parsed)
        return _decimal_to_string(result)
    except (
        SyntaxError,
        ValueError,
        DivisionByZero,
        InvalidOperation,
        ZeroDivisionError,
        RecursionError,
        MemoryError,
        OverflowError,
    ):
        raise ValueError("Invalid input")


@app.post(
    "/calculator",
    response_model=CalculatorResponse,
    responses={400: {"description": "Invalid input", "content": {"application/json": {"schema": {}}}}},
)
def calculate(payload: CalculatorRequest) -> Response:
    try:
        result = evaluate_expression(payload.expression)
    except ValueError:
        return _invalid_input_response()
    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)