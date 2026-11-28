"""Application-wide appearance stored next to the database."""

THEMES = frozenset(
    ("studio", "paper", "book", "print", "console", "polar", "city", "film", "garden")
)


def read_theme(database):
    path = database.with_name("appearance.txt")
    try:
        theme = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "studio"
    return theme if theme in THEMES else "studio"


def write_theme(database, theme):
    if not isinstance(theme, str) or theme not in THEMES:
        raise ValueError("Неизвестная тема")
    path = database.with_name("appearance.txt")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(theme, encoding="utf-8")
    temporary.replace(path)
    return theme
