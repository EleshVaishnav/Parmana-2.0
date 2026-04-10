# ╔══════════════════════════════════════════════════════════════════╗
# ║           PARMANA 2.0 — Skill: Calculator                       ║
# ║  Symbolic math via SymPy + safe numeric eval fallback.          ║
# ║  Self-registers into the global registry on import.             ║
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
import math
import operator
import re
from typing import Any

import yaml
from Skills.registry import Skill, SkillParam, registry

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        with open("config.yaml") as f:
            return yaml.safe_load(f).get("skills", {}).get("calculator", {})
    except Exception:
        return {}

_cfg     = _load_cfg()
_SYMPY   = _cfg.get("use_sympy", True)
_ENABLED = _cfg.get("enabled", True)


# ── Safe Numeric Evaluator ────────────────────────────────────────────────────
# Used as fallback when SymPy is disabled or unavailable.
# Whitelist-only: no exec, no import, no builtins beyond math.

_SAFE_NAMES: dict[str, Any] = {
    # constants
    "pi": math.pi,
    "e":  math.e,
    "tau": math.tau,
    "inf": math.inf,
    # functions
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "cbrt": lambda x: x ** (1/3),
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "degrees": math.degrees,
    "radians": math.radians,
    "ceil": math.ceil,
    "floor": math.floor,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "lcm": getattr(math, "lcm", lambda a, b: abs(a * b) // math.gcd(a, b)),
    "pow": pow,
    "min": min,
    "max": max,
    "sum": sum,
}

def _safe_eval(expression: str) -> str:
    """Evaluate a numeric expression using a whitelist of math functions."""
    # Strip dangerous patterns
    banned = re.compile(
        r'\b(import|exec|eval|open|os|sys|subprocess|__|\bclass\b|\bdef\b)\b'
    )
    if banned.search(expression):
        return "Error: expression contains disallowed tokens."

    try:
        result = eval(expression, {"__builtins__": {}}, _SAFE_NAMES)  # noqa: S307
        return str(result)
    except ZeroDivisionError:
        return "Error: division by zero."
    except Exception as e:
        return f"Error: {e}"


# ── SymPy Handler ─────────────────────────────────────────────────────────────

def _sympy_calculate(expression: str, mode: str) -> str:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr,
        standard_transformations,
        implicit_multiplication_application,
    )

    transformations = standard_transformations + (implicit_multiplication_application,)

    try:
        expr = parse_expr(expression, transformations=transformations)
    except Exception as e:
        # Fall back to safe eval for pure numeric expressions
        return _safe_eval(expression)

    try:
        if mode == "simplify":
            result = sp.simplify(expr)
        elif mode == "expand":
            result = sp.expand(expr)
        elif mode == "factor":
            result = sp.factor(expr)
        elif mode == "solve":
            # Attempt to solve for first free symbol
            symbols = list(expr.free_symbols)
            if not symbols:
                return str(sp.simplify(expr))
            result = sp.solve(expr, symbols[0])
        elif mode == "diff":
            symbols = list(expr.free_symbols)
            if not symbols:
                return "Error: no variable to differentiate with respect to."
            result = sp.diff(expr, symbols[0])
        elif mode == "integrate":
            symbols = list(expr.free_symbols)
            if not symbols:
                return "Error: no variable to integrate."
            result = sp.integrate(expr, symbols[0])
        elif mode == "latex":
            return sp.latex(sp.simplify(expr))
        else:  # "evaluate" — default
            result = sp.simplify(expr)
            # Try to get numeric value if no free symbols
            if not result.free_symbols:
                numeric = float(result.evalf())
                # Return exact form + decimal if they differ
                exact = str(result)
                decimal = f"{numeric:.10g}"
                if exact != decimal:
                    return f"{exact} ≈ {decimal}"
                return decimal

        return str(result)

    except Exception as e:
        return f"Error: {e}"


# ── Main Handler ──────────────────────────────────────────────────────────────

async def calculator(
    expression: str,
    mode: str = "evaluate",
) -> str:
    """
    Evaluate or manipulate a mathematical expression.

    Args:
        expression: Math expression or equation.
                    Examples: "2**32", "sin(pi/4)", "x**2 - 5x + 6", "integrate(x**2)"
        mode:       Operation mode:
                      evaluate  — compute/simplify the expression (default)
                      simplify  — algebraic simplification
                      expand    — expand products/powers
                      factor    — factor polynomials
                      solve     — solve equation for its variable (set = 0)
                      diff      — differentiate w.r.t. first free variable
                      integrate — indefinite integral w.r.t. first free variable
                      latex     — return LaTeX representation

    Returns:
        Result as a string. Exact + decimal for irrational results.
    """
    if not expression or not expression.strip():
        return "Error: empty expression."

    expression = expression.strip()
    logger.debug(f"calculator: expr='{expression}' mode='{mode}'")

    if _SYMPY:
        try:
            return _sympy_calculate(expression, mode)
        except ImportError:
            logger.warning("SymPy not installed — falling back to safe eval.")
            return _safe_eval(expression)
    else:
        return _safe_eval(expression)


async def unit_convert(
    value: float,
    from_unit: str,
    to_unit: str,
) -> str:
    """
    Convert between common units using SymPy's unit system.

    Args:
        value:     Numeric value to convert.
        from_unit: Source unit (e.g. "kg", "miles", "fahrenheit").
        to_unit:   Target unit (e.g. "lbs", "km", "celsius").

    Returns:
        Converted value as a string.
    """
    try:
        import sympy.physics.units as u
        from sympy.physics.units import convert_to
        from sympy.physics.units.util import quantity_simplify
        import sympy as sp

        # Map friendly names to sympy unit objects
        _unit_map = {
            # length
            "m": u.meter, "meter": u.meter, "meters": u.meter,
            "km": u.kilometer, "kilometers": u.kilometer,
            "cm": u.centimeter, "mm": u.millimeter,
            "ft": u.foot, "feet": u.foot, "foot": u.foot,
            "in": u.inch, "inch": u.inch, "inches": u.inch,
            "mi": u.mile, "mile": u.mile, "miles": u.mile,
            "yd": u.yard, "yard": u.yard,
            # mass
            "kg": u.kilogram, "kilogram": u.kilogram,
            "g": u.gram, "gram": u.gram,
            "lb": u.pound, "lbs": u.pound, "pound": u.pound,
            "oz": u.ounce, "ounce": u.ounce,
            # time
            "s": u.second, "sec": u.second, "second": u.second,
            "min": u.minute, "minute": u.minute,
            "hr": u.hour, "hour": u.hour, "hours": u.hour,
            "day": u.day, "days": u.day,
            # temperature (special case)
            "celsius": "celsius", "c": "celsius",
            "fahrenheit": "fahrenheit", "f": "fahrenheit",
            "kelvin": u.kelvin, "k": u.kelvin,
            # speed
            "m/s": u.meter/u.second,
            "km/h": u.kilometer/u.hour, "kph": u.kilometer/u.hour,
            "mph": u.mile/u.hour,
            # data
            "bit": u.bit, "byte": u.byte,
            "kb": u.kibibyte, "mb": u.mebibyte, "gb": u.gibibyte,
        }

        from_key = from_unit.lower().strip()
        to_key   = to_unit.lower().strip()

        # Temperature special case (offset conversion)
        temps = {"celsius", "fahrenheit"}
        if from_key in temps or to_key in temps:
            return _convert_temp(value, from_key, to_key)

        src = _unit_map.get(from_key)
        dst = _unit_map.get(to_key)

        if src is None:
            return f"Error: unknown unit '{from_unit}'."
        if dst is None:
            return f"Error: unknown unit '{to_unit}'."

        quantity = value * src
        result = convert_to(quantity, dst).n()
        numeric = float(result / dst)
        return f"{value} {from_unit} = {numeric:.6g} {to_unit}"

    except ImportError:
        return "Error: SymPy not installed. Run: pip install sympy"
    except Exception as e:
        return f"Conversion error: {e}"


def _convert_temp(value: float, from_u: str, to_u: str) -> str:
    """Handle Celsius ↔ Fahrenheit ↔ Kelvin conversions."""
    # Normalize to Celsius first
    if from_u in ("fahrenheit", "f"):
        celsius = (value - 32) * 5 / 9
    elif from_u in ("kelvin", "k"):
        celsius = value - 273.15
    else:
        celsius = value

    if to_u in ("fahrenheit", "f"):
        result = celsius * 9 / 5 + 32
        return f"{value}°{'C' if from_u in ('celsius','c') else from_u.title()} = {result:.4g}°F"
    elif to_u in ("kelvin", "k"):
        result = celsius + 273.15
        return f"{value}° = {result:.4g} K"
    else:
        return f"{value}° = {celsius:.4g}°C"


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(Skill(
    name="calculator",
    description=(
        "Evaluate math expressions and do symbolic algebra. "
        "Modes: evaluate, simplify, expand, factor, solve, diff, integrate, latex."
    ),
    params=[
        SkillParam(
            name="expression",
            type="string",
            description="Math expression. E.g. '2**32', 'sin(pi/4)', 'x**2 - 5x + 6'.",
            required=True,
        ),
        SkillParam(
            name="mode",
            type="string",
            description="Operation: evaluate | simplify | expand | factor | solve | diff | integrate | latex",
            required=False,
            default="evaluate",
            enum=["evaluate", "simplify", "expand", "factor", "solve", "diff", "integrate", "latex"],
        ),
    ],
    handler=calculator,
    enabled=_ENABLED,
    tags=["math", "compute"],
    timeout=15.0,
))

registry.register(Skill(
    name="unit_convert",
    description="Convert between units of length, mass, time, speed, temperature, and data.",
    params=[
        SkillParam(
            name="value",
            type="number",
            description="Numeric value to convert.",
            required=True,
        ),
        SkillParam(
            name="from_unit",
            type="string",
            description="Source unit (e.g. 'kg', 'miles', 'fahrenheit', 'gb').",
            required=True,
        ),
        SkillParam(
            name="to_unit",
            type="string",
            description="Target unit (e.g. 'lbs', 'km', 'celsius', 'mb').",
            required=True,
        ),
    ],
    handler=unit_convert,
    enabled=_ENABLED,
    tags=["math", "units", "convert"],
    timeout=10.0,
))

logger.debug("calculator + unit_convert skills registered.")
