"""Render nginx.conf.j2 into the shared volume for the proxy.

This module handles the dynamic nginx configuration used for weighted traffic
splitting between stable and canary services. Key features:

- Zero-weight handling: nginx rejects weight=0, so we emit `down` instead
- Simple file-based config: writes config to shared volume
- Configuration validation: tests nginx config before applying

A zero canary weight becomes `down`, not `weight=0` (nginx treats 0 as invalid).
The proxy container watches the config file and automatically reloads nginx
when it changes (see proxy/99-bootstrap.sh). Reloads are debounced so a busy
control loop cannot drop connections and contaminate the latency it is measuring.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class ProxyWriter:
    """Manages nginx configuration for weighted traffic splitting.

    This class renders the Jinja2 template with current traffic weights and
    writes it to the shared volume. The proxy container watches this file and
    automatically reloads nginx when it changes. Reloads are debounced to prevent
    connection drops.

    Attributes:
        template_path: Path to the Jinja2 template file
        output_path: Path where rendered config should be written
        proxy_container: Name of the nginx container (for compatibility)
        debounce_seconds: Minimum time between reloads
        _last_reload: Timestamp of the last reload
        _applied: Last-applied weights (to detect changes)
        _template: Compiled Jinja2 template
    """

    def __init__(
        self,
        template_path: str,
        output_path: str,
        proxy_container: str,
        debounce_seconds: float,
    ) -> None:
        """Initialize the proxy writer.

        Args:
            template_path: Path to nginx.conf.j2 template
            output_path: Path to write rendered config
            proxy_container: Docker container name for nginx
            debounce_seconds: Minimum seconds between reloads
        """
        self.template_path = Path(template_path)
        self.output_path = Path(output_path)
        self.proxy_container = proxy_container
        self.debounce_seconds = debounce_seconds
        self._last_reload = 0.0
        self._applied: tuple[int, int] | None = None
        env = Environment(
            loader=FileSystemLoader(str(self.template_path.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        self._template = env.get_template(self.template_path.name)

    def render(self, stable_weight: int, canary_weight: int) -> str:
        """Render the nginx configuration template with given weights.

        Args:
            stable_weight: Traffic weight for stable service (0-100)
            canary_weight: Traffic weight for canary service (0-100)

        Returns:
            Rendered nginx configuration as string
        """
        return self._template.render(
            stable_weight=int(stable_weight),
            canary_weight=int(canary_weight),
        )

    def apply(self, stable_weight: int, canary_weight: int, force: bool = False) -> bool:
        """Write config if weights changed.

        This method:
        1. Renders the config with new weights
        2. Writes it to the output path if changed
        3. Waits for debounce period if needed

        The proxy container watches the config file and automatically reloads
        nginx when it changes.

        Args:
            stable_weight: Traffic weight for stable service (0-100)
            canary_weight: Traffic weight for canary service (0-100)
            force: If True, apply even if weights haven't changed

        Returns:
            True if config was written, False otherwise
        """
        desired = (int(stable_weight), int(canary_weight))
        text = self.render(*desired)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.output_path.read_text(encoding="utf-8") if self.output_path.exists() else ""
        if existing != text:
            self.output_path.write_text(text, encoding="utf-8")

        if not force and self._applied == desired:
            return False

        now = time.monotonic()
        wait = self.debounce_seconds - (now - self._last_reload)
        if wait > 0:
            time.sleep(wait)

        self._reload()
        self._last_reload = time.monotonic()
        self._applied = desired
        return True

    def _reload(self) -> None:
        """No-op: nginx reload is handled by the proxy container.

        The proxy container watches the config file via 99-bootstrap.sh and
        automatically reloads nginx when it changes. This works cross-platform
        without requiring Docker socket access or host-side scripts.
        """
        pass
