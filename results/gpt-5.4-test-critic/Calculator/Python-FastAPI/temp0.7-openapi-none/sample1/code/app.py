from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation, getcontext
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict


app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

getcontext().prec = 50


class CalculatorRequest(BaseModel):
    expression: str


class CalculatorResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"result": "7"}})
    result: str


class SafeEvaluator:
    def evaluate(self, expression: str) -> str:
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("Expression must be a non-empty string")

        try:
            parsed = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError("Invalid expression syntax") from exc

        try:
            result = self._eval_node(parsed.body)
        except (ValueError, TypeError, DivisionByZero, InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError(str(exc)) from exc

        return self._format_decimal(result)

    def _eval_node(self, node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return self._to_decimal(node.value)
            raise ValueError("Only numeric constants are allowed")

        if isinstance(node, ast.Num):  # pragma: no cover
            return self._to_decimal(node.n)

        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)

            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ValueError("Division by zero")
                return left / right

            raise ValueError("Unsupported operator")

        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)

            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand

            raise ValueError("Unsupported unary operator")

        raise ValueError("Unsupported expression")

    def _to_decimal(self, value: Any) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("Boolean values are not allowed")
        return Decimal(str(value))

    def _format_decimal(self, value: Decimal) -> str:
        if value.is_nan() or value.is_infinite():
            raise ValueError("Result is not a finite number")

        normalized = value.normalize()
        if normalized == normalized.to_integral():
            return format(normalized.quantize(Decimal("1")), "f")
        return format(normalized, "f").rstrip("0").rstrip(".")


evaluator = SafeEvaluator()


@app.post(
    "/calculator",
    response_model=CalculatorResponse,
    responses={
        200: {
            "description": "Ok",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "result": {
                                "type": "string",
                                "example": "7",
                            }
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid input"},
    },
    summary="Calculate the result of an arithmetic expression",
)
async def calculate(payload: CalculatorRequest) -> CalculatorResponse:
    try:
        result = evaluator.evaluate(payload.expression)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)