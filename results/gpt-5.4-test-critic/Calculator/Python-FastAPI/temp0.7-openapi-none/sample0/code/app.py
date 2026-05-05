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


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _evaluate_expression(expression: str) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Expression must be a non-empty string")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid expression") from exc

    def eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return node.value
            raise ValueError("Only numeric constants are allowed")

        if isinstance(node, ast.Num):  # pragma: no cover
            if isinstance(node.n, (int, float)) and not isinstance(node.n, bool):
                return node.n
            raise ValueError("Only numeric constants are allowed")

        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)

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
            if isinstance(node.op, ast.FloorDiv):
                if right == 0:
                    raise ValueError("Division by zero")
                return left // right
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise ValueError("Division by zero")
                return left % right
            if isinstance(node.op, ast.Pow):
                return left ** right

            raise ValueError("Unsupported operator")

        if isinstance(node, ast.UnaryOp):
            operand = eval_node(node.operand)

            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand

            raise ValueError("Unsupported unary operator")

        raise ValueError("Unsupported expression")

    result = eval_node(tree)

    if isinstance(result, float) and result.is_integer():
        return str(int(result))
    return str(result)


@app.post(
    "/calculator",
    response_model=CalculatorResponse,
    responses={
        400: {
            "description": "Invalid input",
        }
    },
    summary="Calculate the result of an arithmetic expression",
)
async def calculate(payload: CalculatorRequest) -> CalculatorResponse:
    try:
        result = _evaluate_expression(payload.expression)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)