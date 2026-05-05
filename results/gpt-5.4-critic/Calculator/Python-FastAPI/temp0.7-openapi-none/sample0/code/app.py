from __future__ import annotations

import ast
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict


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


class SafeEvaluator:
    MAX_EXPRESSION_LENGTH = 512
    MAX_AST_NODES = 128
    MAX_AST_DEPTH = 32
    MAX_ABS_INT = 10**12
    MAX_ABS_FLOAT = 10**12
    MAX_POWER_EXPONENT = 12
    MAX_POWER_BASE_ABS = 10**6

    _allowed_binary_operator_types = (
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
    )

    _allowed_unary_operator_types = (
        ast.UAdd,
        ast.USub,
    )

    def evaluate(self, expression: str | None) -> Any:
        if expression is None:
            raise ValueError("Expression must be a non-empty string")
        if not isinstance(expression, str):
            raise ValueError("Expression must be a non-empty string")
        if not expression.strip():
            raise ValueError("Expression must be a non-empty string")
        if len(expression) > self.MAX_EXPRESSION_LENGTH:
            raise ValueError("Expression is too long")

        try:
            parsed = ast.parse(expression, mode="eval")
            self._validate_tree(parsed)
            result = self._eval_node(parsed.body, depth=1)
        except ValueError:
            raise
        except SyntaxError as exc:
            raise ValueError("Invalid expression syntax") from exc
        except ZeroDivisionError as exc:
            raise ValueError("Division by zero") from exc
        except OverflowError as exc:
            raise ValueError("Numeric overflow") from exc
        except (MemoryError, RecursionError, ArithmeticError) as exc:
            raise ValueError("Invalid expression") from exc
        except Exception as exc:
            raise ValueError("Invalid expression") from exc

        if isinstance(result, bool) or not isinstance(result, (int, float)):
            raise ValueError("Expression did not produce a numeric result")

        self._ensure_numeric_bounds(result)
        return result

    def _validate_tree(self, tree: ast.AST) -> None:
        node_count = 0

        def walk(node: ast.AST, depth: int) -> None:
            nonlocal node_count
            node_count += 1
            if node_count > self.MAX_AST_NODES:
                raise ValueError("Expression is too complex")
            if depth > self.MAX_AST_DEPTH:
                raise ValueError("Expression is too deeply nested")

            if isinstance(node, ast.Expression):
                walk(node.body, depth + 1)
                return

            if isinstance(node, ast.Constant):
                self._validate_constant(node.value)
                return

            if isinstance(node, ast.Num):  # pragma: no cover
                self._validate_constant(node.n)
                return

            if isinstance(node, ast.BinOp):
                if isinstance(node.op, ast.Pow):
                    raise ValueError("Unsupported binary operator")
                if not isinstance(node.op, self._allowed_binary_operator_types):
                    raise ValueError("Unsupported binary operator")
                walk(node.left, depth + 1)
                walk(node.right, depth + 1)
                return

            if isinstance(node, ast.UnaryOp):
                if not isinstance(node.op, self._allowed_unary_operator_types):
                    raise ValueError("Unsupported unary operator")
                walk(node.operand, depth + 1)
                return

            raise ValueError("Unsupported expression")

        walk(tree, 0)

    def _validate_constant(self, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Only numeric constants are allowed")
        self._ensure_numeric_bounds(value)

    def _ensure_numeric_bounds(self, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Only numeric values are allowed")
        if isinstance(value, int):
            if abs(value) > self.MAX_ABS_INT:
                raise ValueError("Numeric value is too large")
            return
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("Numeric overflow")
            if abs(value) > self.MAX_ABS_FLOAT:
                raise ValueError("Numeric value is too large")

    def _safe_add(self, left: int | float, right: int | float) -> int | float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        result = left + right
        self._ensure_numeric_bounds(result)
        return result

    def _safe_sub(self, left: int | float, right: int | float) -> int | float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        result = left - right
        self._ensure_numeric_bounds(result)
        return result

    def _safe_mult(self, left: int | float, right: int | float) -> int | float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        result = left * right
        self._ensure_numeric_bounds(result)
        return result

    def _safe_div(self, left: int | float, right: int | float) -> float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        if right == 0:
            raise ValueError("Division by zero")
        result = left / right
        self._ensure_numeric_bounds(result)
        return result

    def _safe_floordiv(self, left: int | float, right: int | float) -> int | float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        if right == 0:
            raise ValueError("Division by zero")
        result = left // right
        self._ensure_numeric_bounds(result)
        return result

    def _safe_mod(self, left: int | float, right: int | float) -> int | float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        if right == 0:
            raise ValueError("Division by zero")
        result = left % right
        self._ensure_numeric_bounds(result)
        return result

    def _safe_pow(self, left: int | float, right: int | float) -> int | float:
        self._ensure_numeric_bounds(left)
        self._ensure_numeric_bounds(right)
        if isinstance(right, float) and not right.is_integer():
            raise ValueError("Unsupported binary operator")
        exponent = int(right)
        if abs(exponent) > self.MAX_POWER_EXPONENT:
            raise ValueError("Numeric value is too large")
        if abs(left) > self.MAX_POWER_BASE_ABS:
            raise ValueError("Numeric value is too large")
        result = left**exponent
        self._ensure_numeric_bounds(result)
        return result

    def _eval_node(self, node: ast.AST, depth: int) -> Any:
        if depth > self.MAX_AST_DEPTH:
            raise ValueError("Expression is too deeply nested")

        if isinstance(node, ast.Constant):
            self._validate_constant(node.value)
            return node.value

        if isinstance(node, ast.Num):  # pragma: no cover
            self._validate_constant(node.n)
            return node.n

        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left, depth + 1)
            right = self._eval_node(node.right, depth + 1)

            if isinstance(node.op, ast.Add):
                return self._safe_add(left, right)
            if isinstance(node.op, ast.Sub):
                return self._safe_sub(left, right)
            if isinstance(node.op, ast.Mult):
                return self._safe_mult(left, right)
            if isinstance(node.op, ast.Div):
                return self._safe_div(left, right)
            if isinstance(node.op, ast.FloorDiv):
                return self._safe_floordiv(left, right)
            if isinstance(node.op, ast.Mod):
                return self._safe_mod(left, right)
            if isinstance(node.op, ast.Pow):
                return self._safe_pow(left, right)

            raise ValueError("Unsupported binary operator")

        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand, depth + 1)
            self._ensure_numeric_bounds(operand)

            if isinstance(node.op, ast.UAdd):
                result = +operand
            elif isinstance(node.op, ast.USub):
                result = -operand
            else:
                raise ValueError("Unsupported unary operator")

            self._ensure_numeric_bounds(result)
            return result

        raise ValueError("Unsupported expression")


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
                        "type": "Object",
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
async def calculate(request: CalculatorRequest) -> CalculatorResponse:
    try:
        result = evaluator.evaluate(request.expression)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid input") from exc

    return CalculatorResponse(result=format_result(result))


def format_result(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


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
    request_schema = (
        path_item.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if "$ref" in request_schema:
        ref_name = request_schema["$ref"].rsplit("/", 1)[-1]
        component_schema = openapi_schema.get("components", {}).get("schemas", {}).get(ref_name, {})
        component_schema.pop("required", None)

    response_schema = (
        path_item.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    response_schema["type"] = "Object"

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)