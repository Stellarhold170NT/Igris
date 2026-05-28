"""Generate Coral manifest YAML for @coralapi bridge sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.coral_api.models import CoralApiFunction


def generate_bridge_manifest(func: CoralApiFunction, bridge_url: str) -> str:
    """Generate a Coral manifest YAML for an @coralapi function.

    Creates an HTTP backend manifest that points to the bridge server.
    """
    endpoint_url = f"{bridge_url.rstrip('/')}/coral-api/{func.name}"

    manifest: dict[str, Any] = {
        "name": func.name,
        "version": "1.0.0",
        "dsl_version": 3,
        "backend": "http",
        "description": func.description,
        "base_url": endpoint_url,
        "auth": {"type": "HeaderAuth", "headers": []},
        "tables": [
            {
                "name": func.name,
                "description": func.description,
                "request": {
                    "method": "GET",
                    "path": "/",
                    "query": [{"name": k, "from": "filter", "key": k} for k in func.filters],
                },
                "response": {"rows_path": []},
                "pagination": {"mode": "none"},
                "columns": [
                    {
                        "name": col_name,
                        "type": col.type,
                        "nullable": col.nullable,
                        "description": col.description,
                        "expr": {"kind": "path", "path": [col_name]},
                    }
                    for col_name, col in func.columns.items()
                ],
                "filters": [{"name": k, "required": v.required} for k, v in func.filters.items()],
            }
        ],
    }

    return yaml.safe_dump(manifest, sort_keys=False)


def write_bridge_manifests(
    functions: dict[str, CoralApiFunction],
    bridge_url: str,
    output_dir: str,
) -> list[str]:
    """Write manifest YAML files for all @coralapi functions.

    Returns list of written file paths.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    written_files: list[str] = []
    for func in functions.values():
        manifest_yaml = generate_bridge_manifest(func, bridge_url)
        output_file = output_path / f"{func.name}.yaml"
        output_file.write_text(manifest_yaml, encoding="utf-8")
        written_files.append(str(output_file))

    return written_files
