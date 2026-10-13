"""Queue worker. Run one CPU worker on ai-server and one GPU dispatcher (gpu mode) anywhere;
GPU runs are serialised because the DGX shares 128 GB between the 3D models and the LLMs.

    python -m hub.worker --gpu        # dispatches GPU runs one at a time
    python -m hub.worker --cpu        # parametric / conversion runs
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import time

from .db import init_engine, session
from .service import claim, execute

_stop = False


def _halt(*_):
    global _stop
    _stop = True


def loop(gpu: bool | None, poll: float = 2.0, once: bool = False) -> int:
    init_engine()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{'gpu' if gpu else 'cpu' if gpu is False else 'any'}"
    done = 0
    while not _stop:
        with session() as db:
            run = claim(db, worker_id, gpu=gpu)
            if run is not None:
                execute(db, run)
                done += 1
                continue
        if once:
            break
        time.sleep(poll)
    return done


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--gpu", action="store_true")
    g.add_argument("--cpu", action="store_true")
    p.add_argument("--once", action="store_true", help="drain the queue and exit")
    a = p.parse_args()
    signal.signal(signal.SIGTERM, _halt)
    signal.signal(signal.SIGINT, _halt)
    loop(True if a.gpu else False if a.cpu else None, once=a.once)


if __name__ == "__main__":
    main()
