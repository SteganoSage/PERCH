"""Render nginx.conf.j2 into the volume shared with the proxy container.

Reload mechanism: the controller and the proxy both mount ./proxy-conf. The proxy polls
default.conf once a second (proxy/99-bootstrap.sh) and runs `nginx -t && nginx -s reload` when it
changes. Writing the file therefore *is* the reload, so the debounce gates the write: a busy
control loop cannot reload nginx more than once per debounce interval, which would drop
connections and contaminate the latency it is measuring.

A zero weight becomes `down`, not `weight=0` (nginx rejects 0).
"""

from __future__ import annotations

import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class ProxyWriter:
    """Writes weighted nginx configs for the proxy to pick up, at most once per debounce."""

    def __init__(self, template_path: str, output_path: str, debounce_seconds: float) -> None:
        self.template_path = Path(template_path)
        self.output_path = Path(output_path)
        self.debounce_seconds = debounce_seconds
        self._last_write = float("-inf")
        self._applied: tuple[int, int] | None = None
        env = Environment(
            loader=FileSystemLoader(str(self.template_path.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        self._template = env.get_template(self.template_path.name)

    def render(self, stable_weight: int, canary_weight: int) -> str:
        return self._template.render(
            stable_weight=int(stable_weight),
            canary_weight=int(canary_weight),
        )

    def apply(self, stable_weight: int, canary_weight: int, force: bool = False) -> bool:
        """Write the config for these weights. Returns False if nothing needed writing.

        Unchanged weights are skipped unless `force` (used at controller start, when the file on
        disk may be from a previous run). A write within `debounce_seconds` of the last one
        waits out the remainder first.
        """
        desired = (int(stable_weight), int(canary_weight))
        if not force and desired == self._applied:
            return False

        wait = self.debounce_seconds - (time.monotonic() - self._last_write)
        if wait > 0:
            time.sleep(wait)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(self.render(*desired), encoding="utf-8")
        self._last_write = time.monotonic()
        self._applied = desired
        return True
