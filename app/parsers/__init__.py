from . import delta, american, southwest, generic

PARSERS = {
    "auto": None,
    "delta": delta,
    "american": american,
    "southwest": southwest,
    "generic": generic,
}


def select_parser(text: str, requested: str = "auto"):
    requested = (requested or "auto").lower()
    if requested in PARSERS and requested != "auto":
        return PARSERS[requested], requested
    candidates = [
        ("delta", delta, delta.detect(text)),
        ("american", american, american.detect(text)),
        ("southwest", southwest, southwest.detect(text)),
        ("generic", generic, generic.detect(text)),
    ]
    candidates.sort(key=lambda x: x[2], reverse=True)
    name, module, _score = candidates[0]
    if _score < .2:
        raise ValueError("Airline detection was uncertain")
    return module, name
