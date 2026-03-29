from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Mapping

DAY_KEYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
DEFAULT_SLOT_DURATION_MINUTES = 60
DEFAULT_SLOT_START_HOUR = 8
DEFAULT_SLOT_END_HOUR = 20


def _normalize_time_token(token: Any) -> str:
    if token is None:
        return ''
    raw = str(token).strip()
    if not raw:
        return ''
    parts = raw.split(':')
    if len(parts) != 2:
        return ''
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return ''
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return ''
    return f'{hour:02d}:{minute:02d}'


def _normalize_time_list(values: Iterable[Any]) -> list[str]:
    normalized = {_normalize_time_token(value) for value in values}
    normalized.discard('')
    return sorted(normalized)


def _ensure_day_config(value: Mapping[str, Any] | None = None) -> dict:
    base = {
        'enabled': True,
        'all_day': True,
        'times': [],
    }
    if not value:
        return base
    enabled = value.get('enabled')
    times = value.get('times') or []
    normalized = _normalize_time_list(times)
    base['enabled'] = bool(enabled if enabled is not None else base['enabled'])
    base['times'] = normalized
    base['all_day'] = bool(value.get('all_day', not normalized))
    if normalized:
        base['all_day'] = False
    return base


def _build_days(source: Mapping[str, Any] | None = None) -> dict:
    days = {day: _ensure_day_config() for day in DAY_KEYS}
    if not source:
        return days
    for raw_key, raw_value in source.items():
        key = str(raw_key).lower()
        if key in DAY_KEYS:
            days[key] = _ensure_day_config(raw_value if isinstance(raw_value, Mapping) else {})
    return days


def _build_specific_dates(source: Mapping[str, Any] | None = None) -> dict:
    specific = {}
    if not source:
        return specific
    for raw_date, raw_value in source.items():
        key = str(raw_date).strip()
        if not key:
            continue
        specific[key] = _ensure_day_config(raw_value if isinstance(raw_value, Mapping) else {})
    return specific


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value or '{}')
        except json.JSONDecodeError:
            return {}
    if isinstance(value, Mapping):
        return value
    return {}


def _normalize_date_range(value: Any) -> dict | None:
    if not value or not isinstance(value, Mapping):
        return None
    start = str(value.get('start_date') or '').strip()
    end = str(value.get('end_date') or '').strip()
    if not start or not end:
        return None
    return {
        'start_date': start,
        'end_date': end,
    }


def create_default_availability(**overrides: Any) -> dict:
    timezone = overrides.get('timezone') or 'UTC'
    slot_duration = overrides.get('slot_duration_minutes') or DEFAULT_SLOT_DURATION_MINUTES
    date_range = overrides.get('date_range')
    normalized_range = _normalize_date_range(date_range)
    return {
        'timezone': timezone,
        'slot_duration_minutes': int(slot_duration) if slot_duration else DEFAULT_SLOT_DURATION_MINUTES,
        'date_range': normalized_range,
        'days': _build_days(overrides.get('days')),
        'specific_dates': _build_specific_dates(overrides.get('specific_dates')),
    }


def normalize_availability_payload(raw: Any = None) -> dict:
    payload = _parse_json(raw)
    if not isinstance(payload, Mapping):
        return create_default_availability()
    timezone = payload.get('timezone') or 'UTC'
    slot_duration = payload.get('slot_duration_minutes') or DEFAULT_SLOT_DURATION_MINUTES
    normalized_range = _normalize_date_range(payload.get('date_range'))
    return {
        'timezone': timezone,
        'slot_duration_minutes': int(slot_duration) if isinstance(slot_duration, (int, float)) and slot_duration > 0 else DEFAULT_SLOT_DURATION_MINUTES,
        'date_range': normalized_range,
        'days': _build_days(payload.get('days')),
        'specific_dates': _build_specific_dates(payload.get('specific_dates')),
    }


def format_date_key(value: date | datetime) -> str:
    return value.strftime('%Y-%m-%d')


def get_day_key(value: date | datetime) -> str:
    weekday = value.weekday()
    return DAY_KEYS[weekday]


def generate_time_slots(slot_duration_minutes: int, start_hour: int = DEFAULT_SLOT_START_HOUR, end_hour: int = DEFAULT_SLOT_END_HOUR) -> list[str]:
    duration = max(5, slot_duration_minutes or DEFAULT_SLOT_DURATION_MINUTES)
    steps = int(((end_hour - start_hour) * 60) / duration)
    slots: list[str] = []
    for index in range(steps + 1):
        total_minutes = start_hour * 60 + index * duration
        if total_minutes > end_hour * 60:
            break
        hour = int(total_minutes / 60)
        minute = int(total_minutes % 60)
        slots.append(f'{hour:02d}:{minute:02d}')
    return slots


def get_availability_entry(availability: dict, target: date | datetime) -> dict:
    key = format_date_key(target)
    return availability['specific_dates'].get(key) or availability['days'].get(get_day_key(target))


def get_allowed_times(availability: dict, entry: dict) -> set[str]:
    if not entry:
        return set()
    if entry.get('all_day'):
        return set(generate_time_slots(availability.get('slot_duration_minutes', DEFAULT_SLOT_DURATION_MINUTES)))
    return set(entry.get('times', []))
