from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any, Optional

from gpt_index.callbacks.base import BaseCallbackHandler
from gpt_index.callbacks.schema import (
    CBEvent,
    CBEventType,
    EventStats,
    TIMESTAMP_FORMAT,
)


class LlamaDebugHandler(BaseCallbackHandler):
    """Callback handler that keeps track of debug info.

    NOTE: this is a beta feature. The usage within our codebase, and the interface
    may change.

    This handler simply keeps track of event starts/ends, separated by event types.
    You can use this callback handler to keep track of and debug events.

    Args:
        event_starts_to_ignore (Optional[List[CBEventType]]): list of event types to
            ignore when tracking event starts.
        event_ends_to_ignore (Optional[List[CBEventType]]): list of event types to
            ignore when tracking event ends.

    """

    def __init__(
        self,
        event_starts_to_ignore: Optional[List[CBEventType]] = None,
        event_ends_to_ignore: Optional[List[CBEventType]] = None,
    ) -> None:
        """Initialize the llama debug handler."""
        self._events: Dict[CBEventType, List[CBEvent]] = defaultdict(list)
        self._sequential_events: List[CBEvent] = []
        event_starts_to_ignore = (
            event_starts_to_ignore if event_starts_to_ignore else []
        )
        event_ends_to_ignore = event_ends_to_ignore if event_ends_to_ignore else []
        super().__init__(
            event_starts_to_ignore=event_starts_to_ignore,
            event_ends_to_ignore=event_ends_to_ignore,
        )

    def on_event_start(
        self,
        event_type: CBEventType,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any
    ) -> str:
        """Store event start data by event type.

        Args:
            event_type (CBEventType): event type to store.
            payload (Optional[Dict[str, Any]]): payload to store.
            event_id (str): event id to store.

        """
        event = CBEvent(event_type, payload=payload, id_=event_id)
        self._events[event.event_type].append(event)
        self._sequential_events.append(event)
        return event.id_

    def on_event_end(
        self,
        event_type: CBEventType,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any
    ) -> None:
        """Store event end data by event type.

        Args:
            event_type (CBEventType): event type to store.
            payload (Optional[Dict[str, Any]]): payload to store.
            event_id (str): event id to store.

        """
        event = CBEvent(event_type, payload=payload, id_=event_id)
        self._events[event.event_type].append(event)
        self._sequential_events.append(event)

    def get_events(self, event_type: Optional[CBEventType] = None) -> List[CBEvent]:
        """Get all events for a specific event type."""
        if event_type is not None:
            return self._events[event_type]

        return self._sequential_events

    def _get_event_pairs(self, events: List[CBEvent]) -> List[List[CBEvent]]:
        """Helper function to pair events according to their ID."""
        event_pairs: Dict[str, List[CBEvent]] = defaultdict(list)
        for event in events:
            event_pairs[event.id_].append(event)

        sorted_events = sorted(
            event_pairs.values(),
            key=lambda x: datetime.strptime(x[0].time, TIMESTAMP_FORMAT),
        )
        return sorted_events

    def _get_time_stats_from_event_pairs(
        self, event_pairs: List[List[CBEvent]]
    ) -> EventStats:
        """Calculate time-based stats for a set of event pairs"""
        total_secs = 0.0
        for event_pair in event_pairs:
            start_time = datetime.strptime(event_pair[0].time, TIMESTAMP_FORMAT)
            end_time = datetime.strptime(event_pair[-1].time, TIMESTAMP_FORMAT)
            total_secs += (end_time - start_time).total_seconds()

        stats = EventStats(
            total_secs=total_secs,
            average_secs=total_secs / len(event_pairs),
            total_count=len(event_pairs),
        )
        return stats

    def get_event_pairs(
        self, event_type: Optional[CBEventType] = None
    ) -> List[List[CBEvent]]:
        """Pair events by ID, either all events or a sepcific type."""
        if event_type is not None:
            return self._get_event_pairs(self._events[event_type])

        return self._get_event_pairs(self._sequential_events)

    def get_llm_inputs_outputs(self) -> List[List[CBEvent]]:
        """Get the exact LLM inputs and outputs."""
        return self._get_event_pairs(self.events[CBEventType.LLM])

    def get_event_time_info(
        self, event_type: Optional[CBEventType] = None
    ) -> EventStats:
        event_pairs = self.get_event_pairs(event_type)
        return self._get_time_stats_from_event_pairs(event_pairs)

    def flush_event_logs(self) -> None:
        """Clear all events from memory."""
        self._events = defaultdict(list)
        self._sequential_events = []

    @property
    def events(self) -> Dict[CBEventType, List[CBEvent]]:
        return self._events

    @property
    def sequential_events(self) -> List[CBEvent]:
        return self._sequential_events
