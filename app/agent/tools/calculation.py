"""Arithmetic calculation tools for the calculation domain."""

from typing import Any


def add(a: float, b: float) -> dict[str, Any]:
    """Thực hiện phép cộng hai số: a + b."""
    result = a + b
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return {"success": True, "operation": "add", "a": a, "b": b, "result": result}


def subtract(a: float, b: float) -> dict[str, Any]:
    """Thực hiện phép trừ hai số: a - b."""
    result = a - b
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return {"success": True, "operation": "subtract", "a": a, "b": b, "result": result}


def multiply(a: float, b: float) -> dict[str, Any]:
    """Thực hiện phép nhân hai số: a * b."""
    result = a * b
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return {"success": True, "operation": "multiply", "a": a, "b": b, "result": result}


def divide(a: float, b: float) -> dict[str, Any]:
    """Thực hiện phép chia: a / b."""
    if b == 0:
        return {
            "success": False,
            "operation": "divide",
            "a": a,
            "b": b,
            "errorCode": "DIVISION_BY_ZERO",
            "message": "Không thể thực hiện phép chia vì số chia bằng 0.",
        }
    result = a / b
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return {"success": True, "operation": "divide", "a": a, "b": b, "result": result}


__all__ = ["add", "subtract", "multiply", "divide"]
