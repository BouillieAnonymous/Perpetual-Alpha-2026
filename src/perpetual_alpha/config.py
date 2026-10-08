import tomllib


def settings(path):
    with open(path, "rb") as handle:
        config = tomllib.load(handle)["collector"]
    symbols = config["symbols"]
    if not isinstance(symbols, list) or not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("symbols must be a nonempty unique list")
    if any(not isinstance(s, str) or not s.startswith("par:") for s in symbols):
        raise ValueError("Only par markets may be collected")
    for key in ("poll_seconds", "stale_seconds"):
        value = config[key]
        if not isinstance(value, (int, float)) or not 5 <= value <= 3600:
            raise ValueError(f"Invalid {key}")
    return config


