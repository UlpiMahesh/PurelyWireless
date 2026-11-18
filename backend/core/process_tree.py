"""Kill a process and everything it started (Playwright node driver + browsers)."""
from __future__ import annotations

import logging
import time

import psutil

log = logging.getLogger("pw.process")

ProcKey = tuple[int, float]  # (pid, create_time) guards against pid reuse


def key_of(proc: psutil.Process) -> ProcKey | None:
    try:
        return proc.pid, proc.create_time()
    except psutil.Error:
        return None


def descendants_of(pid: int) -> list[ProcKey]:
    try:
        parent = psutil.Process(pid)
        return [k for c in parent.children(recursive=True) if (k := key_of(c))]
    except psutil.Error:
        return []


def _alive(key: ProcKey) -> psutil.Process | None:
    try:
        p = psutil.Process(key[0])
        if abs(p.create_time() - key[1]) > 0.01:
            return None  # pid was reused by something else
        return p if p.is_running() and p.status() != psutil.STATUS_ZOMBIE else None
    except psutil.Error:
        return None


def kill_keys(keys: list[ProcKey]) -> int:
    """Kill the given processes. Returns how many were alive and killed."""
    procs = [p for k in keys if (p := _alive(k))]
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    if procs:
        psutil.wait_procs(procs, timeout=3.0)
    return len(procs)


def kill_tree(pid: int, extra: list[ProcKey] | None = None) -> int:
    """Kill ``pid`` and all of its descendants, plus any previously seen ``extra`` processes."""
    keys: list[ProcKey] = list(extra or [])
    keys += descendants_of(pid)
    try:
        if (k := key_of(psutil.Process(pid))):
            keys.append(k)
    except psutil.Error:
        pass
    n = kill_keys(list(dict.fromkeys(keys)))
    if n:
        log.info("killed %d process(es) for tree rooted at pid %s", n, pid)
    return n


def pid_looks_like_runner(pid: int) -> bool:
    """True if ``pid`` is a live PurelyWireless runner (used for restart recovery)."""
    try:
        return "--run" in psutil.Process(pid).cmdline()
    except psutil.Error:
        return False


def parent_gone(parent_pid: int) -> bool:
    return not psutil.pid_exists(parent_pid)


def wait_gone(pid: int, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)
