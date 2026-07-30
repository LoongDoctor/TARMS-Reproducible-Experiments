"""Run provenance records and submission-evidence gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


class EvidenceGateError(ValueError):
    """Raised when evidence is not eligible for a manuscript artifact."""


@dataclass(frozen=True)
class RunManifest:
    experiment: str
    provenance: str
    run_id: str
    created_at: str
    environment: Mapping[str, Any]
    artifacts: Mapping[str, str]
    schema_version: str = "1.0"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RunManifest":
        required = (
            "schema_version",
            "experiment",
            "provenance",
            "run_id",
            "created_at",
            "environment",
            "artifacts",
        )
        missing = [name for name in required if name not in payload]
        if missing:
            raise ValueError(f"manifest missing required fields: {', '.join(missing)}")
        if not isinstance(payload["environment"], Mapping):
            raise ValueError("environment must be a mapping")
        if not isinstance(payload["artifacts"], Mapping):
            raise ValueError("artifacts must be a mapping")
        return cls(
            schema_version=str(payload["schema_version"]),
            experiment=str(payload["experiment"]),
            provenance=str(payload["provenance"]),
            run_id=str(payload["run_id"]),
            created_at=str(payload["created_at"]),
            environment=dict(payload["environment"]),
            artifacts={str(k): str(v) for k, v in payload["artifacts"].items()},
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment": self.experiment,
            "provenance": self.provenance,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "environment": dict(self.environment),
            "artifacts": dict(self.artifacts),
        }


ELIGIBLE_PROVENANCE = {
    "python": "measured",
    "fabric": "measured_fabric",
    "aamos": "public_secondary",
}


def assert_submission_eligible(manifests: Iterable[RunManifest]) -> None:
    manifests = list(manifests)
    if not manifests:
        raise EvidenceGateError("submission output requires at least one manifest")
    for manifest in manifests:
        expected = ELIGIBLE_PROVENANCE.get(manifest.experiment)
        if expected is None:
            raise EvidenceGateError(
                f"unknown experiment type {manifest.experiment!r} in {manifest.run_id}"
            )
        if manifest.provenance != expected:
            raise EvidenceGateError(
                f"{manifest.experiment} run {manifest.run_id} has provenance "
                f"{manifest.provenance!r}; submission requires {expected!r}"
            )


def load_manifest(path: str | Path) -> RunManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("manifest root must be a JSON object")
    return RunManifest.from_mapping(payload)


def write_manifest(manifest: RunManifest, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest.to_mapping(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
