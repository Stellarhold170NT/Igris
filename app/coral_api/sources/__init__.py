"""Example @coralapi sources demonstrating the bridge pattern."""

from __future__ import annotations

from app.coral_api.decorator import coralapi
from app.coral_api.models import CoralColumn, CoralFilter


@coralapi(
    name="opensre_pods",
    description="Kubernetes pod status from EKS clusters via OpenSRE",
    columns={
        "pod_name": CoralColumn("Utf8", description="Pod name"),
        "namespace": CoralColumn("Utf8", description="K8s namespace"),
        "status": CoralColumn("Utf8", description="Pod phase"),
        "restarts": CoralColumn("Int64", description="Container restart count"),
        "node_name": CoralColumn("Utf8", description="Node where pod is scheduled"),
    },
    filters={
        "cluster": CoralFilter(required=True, description="EKS cluster name"),
        "namespace": CoralFilter(required=False, description="Filter by namespace"),
    },
    source="eks",
    is_available=lambda resolved: "eks" in resolved,
)
def opensre_pods(cluster: str, namespace: str | None = None) -> list[dict]:
    """Return pod data from the EKS client."""
    try:
        from app.services.eks.eks_client import EKSClient  # type: ignore[import-untyped]

        client = EKSClient.from_integration(cluster=cluster)  # type: ignore[attr-defined]
        pods = client.list_pods(cluster=cluster, namespace=namespace)  # type: ignore[attr-defined]
        return [
            {
                "pod_name": p["name"],
                "namespace": p["namespace"],
                "status": p["status"],
                "restarts": p.get("restarts", 0),
                "node_name": p.get("node", ""),
            }
            for p in pods
        ]
    except Exception:
        return []
