"""plain_text source adapter."""


def parse(path, text):
    suffix = path.suffix.lower()
    return (
        [
            dict(source=l.strip(), text="", speaker="", scene="", locator={"line": i})
            for i, l in enumerate(text.splitlines())
            if l.strip()
        ],
        text,
        "text",
    )
