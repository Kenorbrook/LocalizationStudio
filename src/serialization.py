import json, time


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def dump(value):
    return json.dumps(value, ensure_ascii=False)
