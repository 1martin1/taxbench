import ast
import time
from typing import List, Dict

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Configuration constants
MAX_EXPRESSION_LENGTH = 100          # Maximum allowed characters in the expression
MAX_AST_NODES = 100                  # Maximum allowed AST nodes to prevent overly complex expressions
MAX_EXPONENT = 10                    # Maximum absolute value for exponentiation to avoid huge numbers
RATE_LIMIT_REQUESTS = 60            # Max requests per IP
RATE_LIMIT_INTERVAL = 60            # Time window in seconds for rate limiting

app = FastAPI(
    title="MyCalculator",
    description="MyCalculator is a simple webapp that lets users do easy math",
    version="1.0.11",
)

# In‑memory store for rate limiting (IP -> timestamps of recent requests)
_rate_limit_store: Dict[str, List[float]] = {}


def _rate_limit(request: Request):
    """Simple per‑IP rate limiting middleware."""
    client_ip = request.client.host
    now = time.time()
    timestamps = _rate_limit_store.get(client_ip, [])
    # Remove timestamps older than the interval
    timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_INTERVAL]
    if len(timestamps) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Too Many Requests")
    timestamps.append(now)
    _rate_limit_store[client_ip] = timestamps


def _count_ast_nodes(node: ast.AST) -> int:
    """Count total nodes in an AST tree."""
    return 1 + sum(_count_ast_nodes(child) for child in ast.iter_child_nodes(node))


def _eval_node(node: ast.AST) -> float:
    """Recursively evaluate a safe arithmetic AST node."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

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
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            # Prevent extremely large exponentiation
            if isinstance(right, (int, float)) and abs(right) > MAX_EXPONENT:
                raise ValueError("Exponent too large")
            return left ** right
        raise ValueError("Unsupported binary operator")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError("Unsupported unary operator")

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Constants must be int or float")

    # Compatibility for Python <3.8
    if isinstance(node, ast.Num):  # pragma: no cover
        return node.n

    raise ValueError("Unsupported expression element")


class CalcRequest(BaseModel):
    expression: str


class CalcResponse(BaseModel):
    result: str


@app.post(
    "/calculator",
    response_model=CalcResponse,
    responses={400: {"description": "Invalid input"}, 429: {"description": "Too Many Requests"}},
    summary="Calculate the result of an arithmetic expression",
    dependencies=[Depends(_rate_limit)],
)
async def calculate(payload: CalcRequest) -> CalcResponse:
    """
    Evaluate a simple arithmetic expression provided in the request body.
    Only the following operators are allowed: +, -, *, /, %, ** and parentheses.
    """
    expression = payload.expression.strip()

    # Basic length check
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise HTTPException(status_code=400, detail="Expression too long")

    try:
        # Parse expression safely
        parsed = ast.parse(expression, mode="eval")
        # Verify AST size
        if _count_ast_nodes(parsed) > MAX_AST_NODES:
            raise ValueError("Expression too complex")
        # Evaluate
        result = _eval_node(parsed)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail="Invalid input") from exc

    return CalcResponse(result=str(result))


@app.get("/", response_class=HTMLResponse)
async def root():
    html_content = """
    <!doctype html>
    <html>
    <head><title>MyCalculator</title></head>
    <body>
        <h1>MyCalculator</h1>
        <form id="calc-form">
            <input type="text" id="expr" placeholder="Enter expression" size="30"/>
            <button type="submit">Calculate</button>
        </form>
        <pre id="result"></pre>
        <script>
            const form = document.getElementById('calc-form');
            form.addEventListener('submit', async e => {
                e.preventDefault();
                const expr = document.getElementById('expr').value;
                const resp = await fetch('/calculator', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({expression: expr})
                });
                const out = document.getElementById('result');
                if (resp.ok) {
                    const data = await resp.json();
                    out.textContent = 'Result: ' + data.result;
                } else if (resp.status === 429) {
                    out.textContent = 'Error: Too many requests';
                } else {
                    out.textContent = 'Error: Invalid input';
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)