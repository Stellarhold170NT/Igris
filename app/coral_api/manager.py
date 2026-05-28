"""CoralManager: orchestrates source setup, bridge server, and query execution."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

from app.coral_api.bridge import CoralBridgeServer
from app.coral_api.env_mapping import build_coral_env
from app.coral_api.manifest import write_bridge_manifests
from app.coral_api.registry import CoralApiRegistry
from app.coral_api.source_mapping import get_enabled_sources

logger = logging.getLogger(__name__)


class CoralManager:
    """Manages Coral source setup, bridge server, and query execution.

    Lifecycle:
    1. ensure_ready() — set up sources and start bridge if needed
    2. execute_sql(sql) — run a query via Coral CLI
    3. cleanup() — tear down bridge
    """

    def __init__(
        self,
        resolved_integrations: dict[str, dict[str, Any]],
        coral_binary: str = "coral",
        config_dir: str | None = None,
    ) -> None:
        self._resolved = resolved_integrations
        self._coral_binary = coral_binary
        self._config_dir = config_dir or os.path.join(
            os.path.expanduser("~"), ".config", "opensre", "coral_workspace"
        )
        self._bridge: CoralBridgeServer | None = None
        self._sources_installed: set[str] = set()
        self._ready: bool = False

    def ensure_ready(self) -> None:
        if self._ready:
            return

        enabled = get_enabled_sources(self._resolved)

        for name, mapping in enabled.items():
            if name not in self._sources_installed:
                self._install_native_source(name, mapping)

        bridge_funcs = CoralApiRegistry.available(self._resolved)
        if bridge_funcs and self._bridge is None:
            self._bridge = CoralBridgeServer()
            self._bridge.start()
            manifests = write_bridge_manifests(
                bridge_funcs,
                self._bridge.base_url,
                os.path.join(self._config_dir, "manifests"),
            )
            for manifest_path in manifests:
                self._import_manifest(manifest_path)

        self._ready = True

    def execute_sql(self, sql: str, timeout: int = 60) -> dict[str, Any]:
        self.ensure_ready()

        env = dict(os.environ)
        env["CORAL_CONFIG_DIR"] = self._config_dir

        try:
            result = subprocess.run(
                [self._coral_binary, "sql", "--format", "json", sql],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": result.stderr.strip() or f"Coral exited with code {result.returncode}",
                    "sql": sql,
                }

            try:
                data = json.loads(result.stdout)
                return {"ok": True, "data": data, "sql": sql}
            except json.JSONDecodeError:
                return {"ok": True, "raw_output": result.stdout.strip(), "sql": sql}

        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Query timed out after {timeout}s", "sql": sql}
        except FileNotFoundError:
            return {"ok": False, "error": "Coral binary not found", "sql": sql}

    def _install_native_source(self, name: str, mapping: Any) -> None:
        credentials = self._resolved.get(name, {}).get("credentials", {})
        env = build_coral_env(mapping.env_mapping, credentials)

        try:
            result = subprocess.run(
                [self._coral_binary, "source", "add", mapping.coral_name],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, **env, "CORAL_CONFIG_DIR": self._config_dir},
            )
            if result.returncode == 0:
                self._sources_installed.add(name)
                logger.info("Installed Coral source: %s", name)
            else:
                logger.warning("Failed to install Coral source %s: %s", name, result.stderr)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Error installing Coral source %s: %s", name, e)

    def _import_manifest(self, manifest_path: str) -> None:
        try:
            result = subprocess.run(
                [self._coral_binary, "source", "add", "--file", manifest_path],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "CORAL_CONFIG_DIR": self._config_dir},
            )
            if result.returncode == 0:
                logger.info("Imported Coral manifest: %s", manifest_path)
            else:
                logger.warning("Failed to import manifest %s: %s", manifest_path, result.stderr)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Error importing manifest %s: %s", manifest_path, e)

    def cleanup(self) -> None:
        if self._bridge:
            self._bridge.stop()
            self._bridge = None
        self._ready = False
