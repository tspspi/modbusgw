"""Routing rule engine with wildcard support."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass
class RoutingRule:
    frontend: str
    backend: str
    match: Mapping[str, Any]
    unit_override: int | None = None
    mirror_to_mqtt: list[str] = field(default_factory=list)


@dataclass
class RoutingPlan:
    rule: RoutingRule
    backend: str
    unit_id: int
    mirror_to_mqtt: list[str]


class Router:
    def __init__(self) -> None:
        self._rules: list[RoutingRule] = []

    def add_rule(self, rule: RoutingRule) -> None:
        self._rules.append(rule)

    def clear(self) -> None:
        self._rules.clear()

    def resolve(self, request: Mapping[str, Any] | Any) -> RoutingPlan | None:
        """Return the first rule matching the incoming request."""
        for rule in self._rules:
            if self._matches(rule, request):
                unit = rule.unit_override if rule.unit_override is not None else _value(request, 'unit_id')
                return RoutingPlan(rule=rule, backend=rule.backend, unit_id=unit, mirror_to_mqtt=list(rule.mirror_to_mqtt))
        return None

    def _matches(self, rule: RoutingRule, request: Mapping[str, Any] | Any) -> bool:
        if rule.frontend != _value(request, 'frontend'):
            return False
        match = rule.match
        if not _match_list(match.get('unit_ids'), _value(request, 'unit_id')):
            return False
        if not _match_list(match.get('function_codes'), _value(request, 'function_code')):
            return False
        if not _match_operations(match.get('operations'), _value(request, 'operation')):
            return False
        if not _match_range(match.get('register_range'), _value(request, 'register')):
            return False
        return True


def _value(request: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(request, Mapping):
        return request.get(key)
    return getattr(request, key, None)


def _match_list(pattern: Iterable[Any] | None, value: Any) -> bool:
    if pattern is None:
        return True
    if value is None:
        return False
    for candidate in pattern:
        if candidate == '*' or candidate == value:
            return True
    return False


def _match_operations(pattern: Iterable[str] | None, value: str | None) -> bool:
    if pattern is None:
        return True
    if value is None:
        return False
    return value in pattern


def _match_range(rng: Mapping[str, Any] | None, value: Any) -> bool:
    if rng is None:
        return True
    if value is None:
        return False
    start = rng.get('start')
    end = rng.get('end')
    return (start is None or value >= start) and (end is None or value <= end)
