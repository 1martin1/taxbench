from __future__ import annotations

import ast
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict


app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class CalculatorRequest(BaseModel):
    expression: str


class CalculatorResponse(BaseModel):
    result: str
    model_config = ConfigDict(json_schema_extra={"example": {"result": "7"}})


class SafeEvaluator:
    _binary_operators = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a ** b,
    }

    _unary_operators = {
        ast.UAdd: lambda a: +a,
        ast.USub: lambda a: -a,
    }

    def evaluate(self, expression: str) -> str:
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("Expression must be a non-empty string")

        try:
            parsed = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError("Invalid arithmetic expression") from exc

        result = self._eval_node(parsed.body)
        return self._format_result(result)

    def _eval_node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numeric constants are allowed")

        if isinstance(node, ast.Num):
            return node.n

        if isinstance(node, ast.BinOp):
            operator_type = type(node.op)
            if operator_type not in self._binary_operators:
                raise ValueError("Unsupported operator")
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            try:
                return self._binary_operators[operator_type](left, right)
            except ZeroDivisionError as exc:
                raise ValueError("Division by zero") from exc

        if isinstance(node, ast.UnaryOp):
            operator_type = type(node.op)
            if operator_type not in self._unary_operators:
                raise ValueError("Unsupported unary operator")
            operand = self._eval_node(node.operand)
            return self._unary_operators[operator_type](operand)

        raise ValueError("Invalid arithmetic expression")

    @staticmethod
    def _format_result(value: Any) -> str:
        return str(value)


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
                            "result": {"type": "string", "example": "7"}
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid input"},
    },
    summary="Calculate the result of an arithmetic expression",
)
async def calculate(request: CalculatorRequest) -> CalculatorResponse:
    try:
        result = evaluator.evaluate(request.expression)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)