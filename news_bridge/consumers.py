from __future__ import annotations

from typing import Callable

from .file_bus import DailyJsonBus
from .models import ExecutionReport


class JsonSignalConsumer:
    def __init__(self, bus: DailyJsonBus, signal_file_name: str, consumer_name: str) -> None:
        self.bus = bus
        self.signal_file_name = signal_file_name
        self.consumer_name = consumer_name

    def run_once(self, handler: Callable[[dict], dict]) -> int:
        items = self.bus.read_items(self.signal_file_name)
        state = self.bus.get_consumer_state()
        offsets = state.setdefault("offsets", {})
        start = int(offsets.get(self.consumer_name, 0))
        processed = 0

        for idx in range(start, len(items)):
            signal = items[idx]
            result = handler(signal)
            report = ExecutionReport(
                signal_id=signal.get("signal_id", ""),
                broker=str(result.get("broker", "")),
                symbol=str(result.get("symbol") or result.get("underlying") or ""),
                status=str(result.get("status", "UNKNOWN")),
                detail=result,
            )
            self.bus.append_item("execution_reports", report.to_dict())
            offsets[self.consumer_name] = idx + 1
            self.bus.set_consumer_offset(self.consumer_name, idx + 1)
            processed += 1
        return processed
