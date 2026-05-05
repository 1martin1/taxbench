import ast
import asyncio
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)


class CalcRequest(BaseModel):
    expression: str = Field(..., example="1 + 2*3")


class CalcResponse(BaseModel):
    result: str = Field(..., example="7")


# Allowed AST node types (removed non‑existent ast.Paren)
ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Num,          # Python <3.8
    ast.Constant,    # Python >=3.8
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Load,
    ast.Expr,
)


MAX_EXPRESSION_LENGTH = 100          # characters
MAX_AST_NODES = 100                  # limit complexity
MAX_EXPONENT = 10                    # limit exponent size


def _eval_node(node: ast.AST) -> float:
    """Recursively evaluate a validated AST node."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):  # Python 3.8+
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Invalid constant")

    if isinstance(node, ast.Num):  # Python <3.8
        return node.n

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            # Exponentiation already validated in safe_eval
            return left ** right

        raise ValueError("Unsupported binary operator")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)

        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand

        raise ValueError("Unsupported unary operator")

    raise ValueError("Unsupported expression element")


def safe_eval(expr: str) -> float:
    """
    Parse and safely evaluate a simple arithmetic expression.
    Enforces length, node‑count, and exponent limits to mitigate
    resource‑exhaustion attacks.
    """
    if not isinstance(expr, str):
        raise ValueError("Expression must be a string")

    expr = expr.strip()
    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise ValueError("Expression too long")

    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ValueError("Invalid syntax")

    node_counter = 0
    for node in ast.walk(parsed):
        node_counter += 1
        if node_counter > MAX_AST_NODES:
            raise ValueError("Expression too complex")
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError(f"Disallowed element: {type(node).__name__}")

        # Additional constraints for exponentiation
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            # Right operand must be a small constant
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, (int, float)):
                if abs(node.right.value) > MAX_EXPONENT:
                    raise ValueError("Exponent too large")
            else:
                # Non‑constant exponents are disallowed
                raise ValueError("Exponent must be a constant")

    return _eval_node(parsed)


@app.post(
    "/calculator",
    response_model=CalcResponse,
    responses={400: {"description": "Invalid input"}},
)
async def calculate(request: CalcRequest):
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, safe_eval, request.expression),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expression evaluation timed out",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid expression",
        )

    # Convert result to string, stripping unnecessary .0
    if isinstance(result, (int, float)):
        result_str = str(int(result)) if result == int(result) else str(result)
    else:
        result_str = str(result)

    return CalcResponse(result=result_str)


HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyCalculator</title>
</head>
<body>
    <h1>MyCalculator</h1>
    <form id="calc-form">
        <input type="text" id="expression" placeholder="Enter expression" size="40"/>
        <button type="submit">Calculate</button>
    </form>
    <p id="output"></p>
    <script>
        const form = document.getElementById('calc-form');
        const output = document.getElementById('output');
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const expr = document.getElementById('expression').value;
            try {
                const resp = await fetch('/calculator', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({expression: expr})
                });
                if (!resp.ok) {
                    const err = await resp.json();
                    output.textContent = 'Error: ' + err.detail;
                    return;
                }
                const data = await resp.json();
                output.textContent = 'Result: ' + data.result;
            } catch (err) {
                output.textContent = 'Error: ' + err;
            }
        });
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_TEMPLATE


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)