"""Tiny form layer: field specs rendered by templates/form.html and parsed here."""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"  # text textarea int float select bool date time email password file section
    required: bool = False
    options: list = field(default_factory=list)  # [(value, label)]
    help: str = ""
    min: float | None = None
    max: float | None = None
    placeholder: str = ""
    maxlength: int = 200
    blank_none: bool = False  # empty numeric input -> None instead of error / 0
    width: str = ""  # "half" to place two fields per row


def section(title, help=""):
    return Field(name="", label=title, type="section", help=help)


def parse(fields, form):
    """Return (values, errors) for the submitted form."""
    values, errors = {}, {}
    for f in fields:
        if f.type in ("section", "file"):
            continue
        if f.type == "bool":
            values[f.name] = 1 if form.get(f.name) else 0
            continue
        raw = (form.get(f.name) or "").strip()
        if not raw:
            if f.required:
                errors[f.name] = "This field is required."
            elif f.type in ("int", "float"):
                values[f.name] = None if f.blank_none else 0
            else:
                values[f.name] = ""
            continue
        try:
            if f.type == "int":
                value = int(raw)
            elif f.type == "float":
                value = round(float(raw), 2)
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError
            elif f.type == "date":
                value = date.fromisoformat(raw).isoformat()
            elif f.type == "time":
                hh, mm = raw.split(":")[:2]
                if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError
                value = f"{int(hh):02d}:{int(mm):02d}"
            elif f.type == "select":
                if raw not in {str(v) for v, _ in f.options}:
                    raise ValueError
                value = raw
            else:
                if len(raw) > (f.maxlength if f.type != "textarea" else 2000):
                    errors[f.name] = "Too long."
                    continue
                if f.type == "email" and "@" not in raw:
                    raise ValueError
                value = raw
        except ValueError:
            errors[f.name] = "Please enter a valid value."
            continue
        if f.type in ("int", "float"):
            if f.min is not None and value < f.min:
                errors[f.name] = f"Must be at least {f.min:g}."
                continue
            if f.max is not None and value > f.max:
                errors[f.name] = f"Must be at most {f.max:g}."
                continue
        values[f.name] = value
    return values, errors
