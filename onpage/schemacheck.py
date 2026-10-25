"""Minimal JSON Schema checker for the onpage schemas (no extra dependency).

Supports what onpage/schema/*.json use: type (incl. lists and "null"), required, properties,
additionalProperties false, items, minItems, maxItems, enum, const, pattern, minLength, maxLength.
validate(instance, schema) -> list of error strings (empty = valid).
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(inst, sch, path="$"):
    errs = []
    t = sch.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(inst, x) for x in ts):
            return [f"{path}: expected {t}, got {type(inst).__name__}"]
    if "const" in sch and inst != sch["const"]:
        errs.append(f"{path}: must be {sch['const']!r}")
    if "enum" in sch and inst not in sch["enum"]:
        errs.append(f"{path}: {inst!r} not in {sch['enum']}")
    if isinstance(inst, str):
        if "pattern" in sch and not re.search(sch["pattern"], inst):
            errs.append(f"{path}: {inst!r} does not match {sch['pattern']}")
        if len(inst) < sch.get("minLength", 0):
            errs.append(f"{path}: shorter than {sch['minLength']}")
        if "maxLength" in sch and len(inst) > sch["maxLength"]:
            errs.append(f"{path}: longer than {sch['maxLength']}")
    if isinstance(inst, dict):
        for k in sch.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing {k!r}")
        props = sch.get("properties", {})
        if sch.get("additionalProperties") is False:
            for k in inst:
                if k not in props:
                    errs.append(f"{path}: unexpected key {k!r}")
        for k, s in props.items():
            if k in inst:
                errs += validate(inst[k], s, f"{path}.{k}")
    if isinstance(inst, list):
        if len(inst) < sch.get("minItems", 0):
            errs.append(f"{path}: fewer than {sch['minItems']} items")
        if "maxItems" in sch and len(inst) > sch["maxItems"]:
            errs.append(f"{path}: more than {sch['maxItems']} items")
        if "items" in sch:
            for i, v in enumerate(inst):
                errs += validate(v, sch["items"], f"{path}[{i}]")
    return errs


def load(name):
    with open(os.path.join(HERE, "schema", name), encoding="utf-8") as f:
        return json.load(f)
