"""Worker contract (Tech Spec §5): prepare -> execute -> inspect -> return.

`inspect` is shared (hub.inspect_mesh), so adapters implement prepare and execute only.
Adding a model = adding an adapter. Nothing else in the hub changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class InputError(ValueError):
    """The inputs do not suit this adapter (wrong view count, missing prompt...)."""


class PolicyError(PermissionError):
    """The request breaks a governance rule (e.g. internal images to a hosted service)."""


@dataclass
class OutputFile:
    filename: str
    data: bytes
    role: str = "model"


@dataclass
class ExecResult:
    files: list[OutputFile]
    worker_info: dict = field(default_factory=dict)


@dataclass
class Adapter:
    name: str
    label: str
    workflows: tuple[str, ...]  # image, multiview, text, parametric, reconstruction, import
    licence: str
    host: str
    evidence_class: str = "generated-visual"
    needs_gpu: bool = True
    # True when inputs leave Equinix-controlled systems. Such adapters only accept `public` inputs.
    data_egress: bool = False
    notes: str = ""

    def available(self, settings) -> tuple[bool, str]:
        return True, ""

    def prepare(self, *, workflow: str, prompt: str, images: list[dict], params: dict) -> dict:
        if workflow not in self.workflows:
            raise InputError(f"{self.name} does not support {workflow}")
        if workflow == "text" and not prompt.strip():
            raise InputError("text workflow needs a prompt")
        if workflow == "image" and len(images) < 1:
            raise InputError("image workflow needs one image")
        if workflow == "multiview" and len(images) < 2:
            raise InputError("multi-view workflow needs at least two labelled views")
        return {"workflow": workflow, "prompt": prompt, "images": images, "params": params}

    def execute(self, payload: dict, settings, read_blob) -> ExecResult:  # pragma: no cover
        raise NotImplementedError

    def describe(self, settings) -> dict:
        ok, why = self.available(settings)
        return {
            "name": self.name, "label": self.label, "workflows": list(self.workflows),
            "licence": self.licence, "host": self.host, "evidence_class": self.evidence_class,
            "needs_gpu": self.needs_gpu, "data_egress": self.data_egress,
            "available": ok, "unavailable_reason": why, "notes": self.notes,
        }


def check_egress(adapter: Adapter, classification: str) -> None:
    if adapter.data_egress and classification != "public":
        raise PolicyError(
            f"{adapter.label} sends inputs outside Equinix systems; only inputs classified 'public' "
            "(e.g. the public brand photo library) may be used. VDC and internal photos stay in the lab."
        )
