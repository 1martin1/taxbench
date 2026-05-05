from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

import uvicorn
from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

MAX_EXPRESSION_LENGTH = 512
MAX_AST_NODES = 256
MAX_AST_DEPTH = 64
MAX_NUMERIC_LITERAL_LENGTH = 64
MAX_RESULT_STRING_LENGTH = 256


class CalculatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expression: str | None = None


class CalculatorResponse(BaseModel):
    result: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _bad_request_response() -> JSONResponse:
    return JSONResponse(status_code=400, content={})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Any, exc: RequestValidationError) -> JSONResponse:
    return _bad_request_response()


def _format_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _validate_decimal_string(text: str) -> None:
    if len(text) > MAX_NUMERIC_LITERAL_LENGTH:
        raise ValueError("Invalid arithmetic operation")

    digit_count = sum(1 for ch in text if ch.isdigit())
    if digit_count == 0 or digit_count > MAX_NUMERIC_LITERAL_LENGTH:
        raise ValueError("Invalid arithmetic operation")


def _decimal_from_constant(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean values are not allowed")

    if isinstance(value, int):
        text = str(value)
        _validate_decimal_string(text)
        return Decimal(text)

    if isinstance(value, float):
        text = str(value)
        _validate_decimal_string(text)
        return Decimal(text)

    raise ValueError("Only numeric constants are allowed")


def _validate_ast_limits(root: ast.AST) -> None:
    stack: list[tuple[ast.AST, int]] = [(root, 1)]
    node_count = 0

    while stack:
        node, depth = stack.pop()
        node_count += 1

        if node_count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise ValueError("Expression is too complex")

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError("Boolean values are not allowed")
            if isinstance(node.value, int):
                _validate_decimal_string(str(node.value))
            elif isinstance(node.value, float):
                _validate_decimal_string(str(node.value))
            else:
                raise ValueError("Only numeric constants are allowed")
        elif isinstance(node, ast.Num):  # pragma: no cover
            _validate_decimal_string(str(node.n))
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                raise ValueError("Unsupported operator")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                raise ValueError("Unsupported unary operator")
        elif isinstance(node, ast.Expression):
            pass
        elif isinstance(node, (ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)):
            pass
        else:
            raise ValueError("Unsupported expression")

        for child in ast.iter_child_nodes(node):
            stack.append((child, depth + 1))


def _eval_node(root: ast.AST) -> Decimal:
    stack: list[tuple[ast.AST, bool]] = [(root, False)]
    values: dict[int, Decimal] = {}

    while stack:
        node, visited = stack.pop()

        if not visited:
            stack.append((node, True))

            if isinstance(node, ast.Expression):
                stack.append((node.body, False))
            elif isinstance(node, ast.BinOp):
                stack.append((node.right, False))
                stack.append((node.left, False))
            elif isinstance(node, ast.UnaryOp):
                stack.append((node.operand, False))
            elif isinstance(node, (ast.Constant, ast.Num)):  # pragma: no branch
                pass
            else:
                raise ValueError("Unsupported expression")
            continue

        if isinstance(node, ast.Expression):
            values[id(node)] = values[id(node.body)]

        elif isinstance(node, ast.Constant):
            values[id(node)] = _decimal_from_constant(node.value)

        elif isinstance(node, ast.Num):  # pragma: no cover
            values[id(node)] = _decimal_from_constant(node.n)

        elif isinstance(node, ast.UnaryOp):
            operand = values[id(node.operand)]
            if isinstance(node.op, ast.UAdd):
                result = operand
            elif isinstance(node.op, ast.USub):
                result = -operand
            else:
                raise ValueError("Unsupported unary operator")
            values[id(node)] = result

        elif isinstance(node, ast.BinOp):
            left = values[id(node.left)]
            right = values[id(node.right)]

            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                try:
                    result = left / right
                except DivisionByZero as exc:
                    raise ValueError("Division by zero") from exc
            else:
                raise ValueError("Unsupported operator")

            result_text = format(result, "f") if result.is_finite() else str(result)
            if len(result_text) > MAX_RESULT_STRING_LENGTH:
                raise ValueError("Invalid arithmetic operation")

            values[id(node)] = result

        else:
            raise ValueError("Unsupported expression")

    return values[id(root)]


def evaluate_expression(expression: str | None) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Expression must be a non-empty string")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("Expression is too complex")

    try:
        parsed = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid expression syntax") from exc

    _validate_ast_limits(parsed)

    try:
        result = _eval_node(parsed)
    except (InvalidOperation, OverflowError, RecursionError) as exc:
        raise ValueError("Invalid arithmetic operation") from exc

    formatted = _format_decimal(result)
    if len(formatted) > MAX_RESULT_STRING_LENGTH:
        raise ValueError("Invalid arithmetic operation")
    return formatted


@app.post(
    "/calculator",
    response_model=CalculatorResponse,
    responses={
        400: {
            "description": "Invalid input",
        }
    },
)
async def calculate(payload: CalculatorRequest = Body(...)) -> CalculatorResponse | JSONResponse:
    try:
        result = evaluate_expression(payload.expression)
    except ValueError:
        return _bad_request_response()

    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)