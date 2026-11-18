import time


def run(params, emit):
    """Safe plumbing test. Emits deterministic stages without touching portal data."""
    steps = int(params.get("steps", 4))
    delay = float(params.get("delay_seconds", 0.15))
    if not 1 <= steps <= 20 or not 0 <= delay <= 3:
        raise ValueError("demo steps must be 1-20 and delay_seconds must be 0-3")
    emit({"type": "stage", "stage": "Running demo automation"})
    for index in range(1, steps + 1):
        time.sleep(delay)
        emit({"type": "progress", "progress": round(index * 100 / steps), "stage": f"Demo step {index} of {steps}"})
        emit({"type": "log", "level": "INFO", "message": f"Completed demo step {index}/{steps}"})
    emit({"type": "done", "message": "Demo automation completed"})
