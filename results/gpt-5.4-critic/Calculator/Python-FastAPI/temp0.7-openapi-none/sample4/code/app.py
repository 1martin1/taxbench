from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


MAX_EXPRESSION_LENGTH = 256
MAX_AST_NODES = 128
MAX_AST_DEPTH = 32
MAX_NUMERIC_LITERAL_LENGTH = 64
DECIMAL_PRECISION = 50
MAX_RESULT_STRING_LENGTH = 128


app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class CalculatorRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"expression": "1 + 2*3"}})
    expression: str | None = None


class CalculatorResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"result": "7"}})
    result: str


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    path_item = openapi_schema.get("paths", {}).get("/calculator", {}).get("post", {})
    request_body = (
        path_item.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if request_body:
        request_body["type"] = "object"
        request_body["properties"] = {
            "expression": {
                "type": "string",
                "example": "1 + 2*3",
            }
        }
        request_body.pop("required", None)

    responses = path_item.get("responses", {})
    if "422" in responses:
        responses.pop("422", None)
    response_200_schema = (
        responses.get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if response_200_schema:
        response_200_schema["type"] = "object"
        response_200_schema["properties"] = {
            "result": {
                "type": "string",
                "example": "7",
            }
        }
        response_200_schema["required"] = ["result"]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


def _count_nodes_and_depth(node: ast.AST, depth: int = 0) -> tuple[int, int]:
    count = 1
    max_depth = depth
    for child in ast.iter_child_nodes(node):
        child_count, child_depth = _count_nodes_and_depth(child, depth + 1)
        count += child_count
        if child_depth > max_depth:
            max_depth = child_depth
    return count, max_depth


def _parse_decimal_from_constant(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Invalid input")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = str(value)
    else:
        raise ValueError("Invalid input")

    if len(text.lstrip("-")) > MAX_NUMERIC_LITERAL_LENGTH:
        raise ValueError("Invalid input")

    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid input")


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Invalid input")

    if value == value.to_integral():
        text = str(value.to_integral())
    else:
        normalized = value.normalize()
        text = format(normalized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")

    if len(text) > MAX_RESULT_STRING_LENGTH:
        raise ValueError("Invalid input")
    return text


def _safe_decimal_op(left: Decimal, right: Decimal, operator: ast.operator) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        if isinstance(operator, ast.Add):
            result = left + right
        elif isinstance(operator, ast.Sub):
            result = left - right
        elif isinstance(operator, ast.Mult):
            result = left * right
        elif isinstance(operator, ast.Div):
            result = left / right
        else:
            raise ValueError("Invalid input")

    if not result.is_finite():
        raise ValueError("Invalid input")

    result_text = format(result, "f")
    if len(result_text.replace("-", "").replace(".", "")) > MAX_RESULT_STRING_LENGTH:
        raise ValueError("Invalid input")

    return result


def _eval_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        return _parse_decimal_from_constant(node.value)

    if isinstance(node, ast.Num):  # pragma: no cover
        return _parse_decimal_from_constant(node.n)

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _safe_decimal_op(left, right, node.op)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("Invalid input")

    raise ValueError("Invalid input")


def evaluate_expression(expression: str | None) -> str:
    if expression is None or not isinstance(expression, str) or not expression.strip():
        raise ValueError("Invalid input")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("Invalid input")

    try:
        parsed = ast.parse(expression, mode="eval")
        node_count, max_depth = _count_nodes_and_depth(parsed)
        if node_count > MAX_AST_NODES or max_depth > MAX_AST_DEPTH:
            raise ValueError("Invalid input")

        result = _eval_node(parsed)
        return _format_decimal(result)
    except (
        SyntaxError,
        ValueError,
        TypeError,
        DivisionByZero,
        InvalidOperation,
        ZeroDivisionError,
        RecursionError,
        MemoryError,
    ):
        raise ValueError("Invalid input")


@app.post(
    "/calculator",
    response_model=CalculatorResponse,
    responses={400: {"description": "Invalid input"}},
)
def calculate(payload: CalculatorRequest) -> CalculatorResponse:
    try:
        result = evaluate_expression(payload.expression)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid input")
    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)