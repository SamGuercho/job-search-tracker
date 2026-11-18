"""Action registry: one typed function = one HTTP endpoint = one CLI command = one agent tool.

Decorate a function with `@action("group.verb")`. Its signature (type hints +
defaults) becomes a pydantic input model and a JSON schema, and its docstring
becomes the description. Nothing else needs to be written to expose it.
"""
from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model


@dataclass
class Action:
    name: str
    fn: Callable[..., Any]
    input_model: type[BaseModel]
    description: str
    confirm: bool = False   # destructive/overwriting: agents must get the user's approval first

    def run(self, payload: dict | None = None) -> Any:
        args = self.input_model.model_validate(payload or {})
        # Only pass what the caller sent so defaults stay in the function, and
        # nested "patch" models keep track of which fields were really given.
        kwargs = {k: getattr(args, k) for k in args.model_fields_set}
        return to_jsonable(self.fn(**kwargs))

    def manifest(self) -> dict:
        return {"name": self.name, "description": self.description, "confirm": self.confirm,
                "input_schema": self.input_model.model_json_schema()}


ACTIONS: dict[str, Action] = {}


def action(name: str, confirm: bool = False):
    """Register `fn` as an action. confirm=True marks it as deleting or overwriting data."""
    def register(fn: Callable) -> Callable:
        hints = typing.get_type_hints(fn)
        fields = {}
        for p in inspect.signature(fn).parameters.values():
            ann = hints.get(p.name, Any)
            fields[p.name] = (ann, ... if p.default is inspect.Parameter.empty else p.default)
        model = create_model(f"{fn.__name__}_input", __config__=ConfigDict(extra="forbid"), **fields)
        ACTIONS[name] = Action(name, fn, model, inspect.cleandoc(fn.__doc__ or ""), confirm)
        return fn
    return register


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.view() if hasattr(value, "view") else value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    return value


def run(name: str, payload: dict | None = None) -> Any:
    if name not in ACTIONS:
        raise KeyError(f"Unknown action {name!r}")
    return ACTIONS[name].run(payload)


def manifest() -> list[dict]:
    return [a.manifest() for a in ACTIONS.values()]
