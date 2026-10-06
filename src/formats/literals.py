import ast, re

SPAN = re.compile(r'"(?:\\.|[^"\\])*"')


def literal(s):
    # Ren'Py permits unknown escape sequences (e.g. \%). Preserve them.
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError):
        raise ValueError("Не удалось прочитать строковый литерал")
