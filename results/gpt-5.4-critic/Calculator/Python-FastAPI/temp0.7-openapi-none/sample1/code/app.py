from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation, getcontext
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


getcontext().prec = 50

MAX_EXPRESSION_LENGTH = 1000
MAX_AST_NODES = 300
MAX_AST_DEPTH = 100

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class CalculatorRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    expression: str


class CalculatorResponse(BaseModel):
    result: str


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


class SafeEvaluator:
    @staticmethod
    def evaluate(expression: str) -> str:
        expression = expression.strip()
        if not expression:
            raise ValueError("Expression cannot be empty")
        if len(expression) > MAX_EXPRESSION_LENGTH:
            raise ValueError("Expression is too long")

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError("Invalid expression syntax") from exc
        except RecursionError as exc:
            raise ValueError("Expression is too complex") from exc

        try:
            SafeEvaluator._validate_tree(tree)
            result = SafeEvaluator._eval_node(tree.body)
            return SafeEvaluator._format_decimal(result)
        except (DivisionByZero, InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError("Invalid arithmetic operation") from exc
        except OverflowError as exc:
            raise ValueError("Arithmetic overflow") from exc
        except RecursionError as exc:
            raise ValueError("Expression is too complex") from exc

    @staticmethod
    def _validate_tree(tree: ast.AST) -> None:
        count = 0
        max_depth = 0
        stack: list[tuple[ast.AST, int]] = [(tree, 1)]

        while stack:
            node, depth = stack.pop()
            count += 1
            if count > MAX_AST_NODES:
                raise ValueError("Expression is too complex")
            if depth > max_depth:
                max_depth = depth
            if max_depth > MAX_AST_DEPTH:
                raise ValueError("Expression is too complex")

            if isinstance(node, ast.Expression):
                stack.append((node.body, depth + 1))
            elif isinstance(node, ast.BinOp):
                if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                    raise ValueError("Unsupported operator")
                stack.append((node.right, depth + 1))
                stack.append((node.left, depth + 1))
            elif isinstance(node, ast.UnaryOp):
                if not isinstance(node.op, (ast.UAdd, ast.USub)):
                    raise ValueError("Unsupported unary operator")
                stack.append((node.operand, depth + 1))
            elif isinstance(node, ast.Constant):
                if isinstance(node.value, bool):
                    raise ValueError("Boolean values are not allowed")
                if not isinstance(node.value, (int, float)):
                    raise ValueError("Unsupported constant")
            elif isinstance(node, ast.Num):  # pragma: no cover
                if isinstance(node.n, bool):
                    raise ValueError("Boolean values are not allowed")
                if not isinstance(node.n, (int, float)):
                    raise ValueError("Unsupported constant")
            else:
                raise ValueError("Unsupported expression")

    @staticmethod
    def _eval_node(node: ast.AST) -> Decimal:
        if isinstance(node, ast.BinOp):
            left = SafeEvaluator._eval_node(node.left)
            right = SafeEvaluator._eval_node(node.right)

            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right

            raise ValueError("Unsupported operator")

        if isinstance(node, ast.UnaryOp):
            operand = SafeEvaluator._eval_node(node.operand)

            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand

            raise ValueError("Unsupported unary operator")

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError("Boolean values are not allowed")
            if isinstance(node.value, (int, float)):
                return SafeEvaluator._to_decimal(node.value)
            raise ValueError("Unsupported constant")

        if isinstance(node, ast.Num):  # pragma: no cover
            return SafeEvaluator._to_decimal(node.n)

        raise ValueError("Unsupported expression")

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("Invalid number") from exc

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        if not value.is_finite():
            raise ValueError("Result is not finite")

        normalized = value.normalize()

        if normalized == normalized.to_integral():
            return str(normalized.quantize(Decimal("1")))

        text = format(normalized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text


@app.post("/calculator", response_model=CalculatorResponse, status_code=200)
def calculate(request: CalculatorRequest = Body(...)) -> CalculatorResponse:
    try:
        result = SafeEvaluator.evaluate(request.expression)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CalculatorResponse(result=result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)