"""
ct_toolkit.core.kernel
-----------------------
Constitutional Kernel: Contains the axiomatic anchors that the system can never change
and plastic commitments that can be extended by the user.
"""
from __future__ import annotations

import os
import re
import unicodedata
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from ct_toolkit.core.exceptions import (
    AxiomaticViolationError,
    PlasticConflictError,
    CTToolkitError,
)


# ── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class AxiomaticAnchor:
    id: str
    description: str
    keywords: list = field(default_factory=list)


@dataclass
class PlasticCommitment:
    id: str
    description: str
    default_value: Any = None
    current_value: Any = None
    keywords: list = field(default_factory=list)

    def __post_init__(self):
        if self.current_value is None:
            self.current_value = self.default_value


@dataclass
class KernelProfile:
    name: str
    version: str = "1.0.0"
    description: str = ""
    axiomatic_anchors: list = field(default_factory=list)
    plastic_commitments: list = field(default_factory=list)


# ── Main Kernel Class ──────────────────────────────────────────────────────────

class ConstitutionalKernel:
    """
    The ethical and behavioral core of the system.

    Usage:
        kernel = ConstitutionalKernel.from_yaml("kernels/defense.yaml")
        kernel.validate_user_rule("allow classified data sharing")
    """

    def __init__(self, profile: KernelProfile, is_readonly: bool = False) -> None:
        self._profile = profile
        self.is_readonly = is_readonly

    @classmethod
    def from_yaml(cls, path: Any) -> "ConstitutionalKernel":
        """
        Loads a kernel from a YAML file.
        
        Args:
            path: Path to the YAML file (string, Path object, or Traversable).
            
        Returns:
            ConstitutionalKernel instance.
            
        Raises:
            CTToolkitError: If path traversal is detected.
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If the YAML file is malformed.
        """
        # Path traversal protection - only check for string paths
        if isinstance(path, str):
            resolved_path = os.path.realpath(path)
            base_dir = os.path.realpath(Path(__file__).parent.parent)
            if not resolved_path.startswith(base_dir):
                # Allow loading from user-specified directories but validate
                # that the path doesn't contain traversal sequences
                if ".." in path.split(os.sep):
                    raise CTToolkitError(
                        f"Potential path traversal detected: {path}. "
                        "Path traversal sequences are not allowed for security reasons."
                    )
        
        # Handle different path types - str check first to avoid Pylance errors
        if isinstance(path, str):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        elif hasattr(path, "open") and callable(getattr(path, "open", None)):
            # Path or Traversable objects
            with path.open("r", encoding="utf-8") as f:  # type: ignore[union-attr]
                data = yaml.safe_load(f)
        else:
            raise CTToolkitError(f"Unsupported path type: {type(path).__name__}. Expected str, Path, or Traversable.")
        
        if not isinstance(data, dict):
            data = {}

        anchors = [AxiomaticAnchor(**a) for a in data.get("axiomatic_anchors", [])]
        commitments = [PlasticCommitment(**c) for c in data.get("plastic_commitments", [])]

        profile = KernelProfile(
            name=data.get("name", "custom_kernel"),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            axiomatic_anchors=anchors,
            plastic_commitments=commitments,
        )
        return cls(profile)

    @classmethod
    def default(cls) -> ConstitutionalKernel:
        # Use importlib.resources to find the default kernel regardless of installation path
        try:
            from importlib.resources import files
            # Load built-in kernel using importlib.resources
            kernel_resource = files("ct_toolkit.kernels").joinpath("default.yaml")
            if kernel_resource.is_file(): # Use is_file() for Traversable objects
                return ConstitutionalKernel.from_yaml(kernel_resource)
            else:
                raise FileNotFoundError(f"Default kernel resource not found: {kernel_resource}")
        except (ImportError, FileNotFoundError):
            # Fallback for older python or weird environments
            default_path = Path(__file__).parent.parent / "kernels" / "default.yaml"
            return cls.from_yaml(default_path)

    @property
    def name(self) -> str:
        return self._profile.name

    @property
    def anchors(self) -> list:
        return self._profile.axiomatic_anchors

    @property
    def commitments(self) -> list:
        return self._profile.plastic_commitments

    def get_system_prompt_injection(self) -> str:
        lines = [
            "# Constitutional Identity Kernel",
            "The unchangeable core rules of this system are below:",
            "",
        ]
        for anchor in self.anchors:
            lines.append(f"- {anchor.description}")

        active = [c for c in self.commitments if c.current_value not in (None, False, "")]
        if active:
            lines += ["", "## Active Behavioral Rules"]
            for c in active:
                lines.append(f"- {c.description}: {c.current_value}")

        lines += ["", "---", ""]
        return "\n".join(lines)

    def validate_user_rule(self, rule_text: str) -> None:
        normalized_rule = unicodedata.normalize("NFKC", rule_text).casefold()
        for anchor in self.anchors:
            if self._conflicts_with(normalized_rule, anchor.keywords):
                raise AxiomaticViolationError(rule=rule_text, anchor=anchor.id)
        for commitment in self.commitments:
            if self._conflicts_with(normalized_rule, commitment.keywords):
                raise PlasticConflictError(rule=rule_text, commitment=commitment.id)

    _RULE_SYNONYM_MAP: dict[str, str] = {
        "disable": "disable",
        "deactivate": "disable",
        "remove": "disable",
        "suppress": "disable",
        "eliminate": "disable",
        "oversight": "oversight",
        "monitoring": "oversight",
        "supervision": "oversight",
        "human": "human",
        "operator": "human",
        "auditor": "human",
        "bypass": "bypass",
        "evade": "bypass",
        "circumvent": "bypass",
        "skip": "bypass",
        "ignore": "ignore",
        "disregard": "ignore",
        "forget": "ignore",
        "abandon": "ignore",
        "override": "override",
        "overwrite": "override",
        "previous": "previous",
        "initial": "previous",
        "prior": "previous",
        "original": "previous",
        "values": "values",
        "rules": "values",
        "instructions": "values",
        "principles": "values",
        "constitution": "values",
        "identity": "identity",
        "persona": "identity",
        "deceive": "deceive",
        "mislead": "deceive",
        "trick": "deceive",
        "falsify": "deceive",
        "manipulate": "manipulate",
        "tamper": "manipulate",
        "forge": "manipulate",
        "alter": "manipulate",
        "log": "log",
        "logs": "log",
        "provenance": "log",
        "audit": "log",
        "false": "false",
        "fake": "false",
        "fabricated": "false",
        "report": "report",
        "hide": "hide",
        "conceal": "hide",
        "mask": "hide",
        "behavior": "behavior",
        "actions": "behavior",
        "activity": "behavior",
        "endorsement": "endorsement",
        "approval": "approval",
        "authorization": "approval",
        "validation": "validation",
        "verification": "validation",
        "allow": "allow",
        "permit": "allow",
        "enable": "allow",
        "harmful": "harmful",
        "dangerous": "harmful",
        "malicious": "harmful",
        "unsafe": "harmful",
        "harm": "harmful",
        "safety": "harmful",
        "filter": "filter",
        "filters": "filter",
        "guardrail": "filter",
        "guardrails": "filter",
        "aggressive": "aggressive",
        "hostile": "aggressive",
        "insulting": "aggressive",
        "abusive": "aggressive",
        "rude": "aggressive",
        "tone": "tone",
        "communication": "tone",
        "style": "tone",
    }

    _STOPWORDS: set[str] = {"from", "the", "and", "for", "with", "without", "to", "of", "in", "on"}

    def _conflicts_with(self, rule_text: str, keywords: list) -> bool:
        # SECURITY: Match complete Unicode words rather than arbitrary
        # substrings, plus canonical synonym/paraphrase concept matching.
        rule_words = re.findall(r"\w+", rule_text, flags=re.UNICODE)
        rule_concepts = {self._RULE_SYNONYM_MAP.get(w, w) for w in rule_words}

        for kw in keywords:
            if not isinstance(kw, str) or not kw.strip():
                continue
            normalized_keyword = unicodedata.normalize("NFKC", kw).casefold()
            kw_escaped = re.escape(normalized_keyword)
            if re.search(rf"(?<!\w){kw_escaped}(?!\w)", rule_text, flags=re.UNICODE):
                return True

            # Semantic / paraphrase concept check
            kw_words = [
                w for w in re.findall(r"\w+", normalized_keyword, flags=re.UNICODE)
                if w not in self._STOPWORDS
            ]
            if not kw_words:
                continue
            if len(kw_words) == 1:
                kw_concept = self._RULE_SYNONYM_MAP.get(kw_words[0], kw_words[0])
                if kw_words[0] in self._RULE_SYNONYM_MAP and kw_concept in rule_concepts:
                    return True
            else:
                kw_concepts = {self._RULE_SYNONYM_MAP.get(w, w) for w in kw_words}
                if len(kw_concepts) >= 2 and kw_concepts.issubset(rule_concepts):
                    return True
        return False

    def update_commitment(self, commitment_id: str, new_value: Any) -> None:
        if self.is_readonly:
            raise CTToolkitError(f"Cannot update commitment '{commitment_id}': Kernel is read-only.")
        for c in self._profile.plastic_commitments:
            if c.id == commitment_id:
                c.current_value = new_value
                return
        raise KeyError(f"Commitment not found: '{commitment_id}'")

    def to_dict(self) -> dict[str, Any]:
        """Serializes the kernel to a dictionary."""
        return {
            "name": self._profile.name,
            "version": self._profile.version,
            "description": self._profile.description,
            "axiomatic_anchors": [
                {"id": a.id, "description": a.description, "keywords": a.keywords}
                for a in self.anchors
            ],
            "plastic_commitments": [
                {
                    "id": c.id,
                    "description": c.description,
                    "default_value": c.default_value,
                    "current_value": c.current_value,
                    "keywords": c.keywords
                }
                for c in self.commitments
            ],
            "is_readonly": self.is_readonly
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConstitutionalKernel:
        """Creates a kernel from a dictionary."""
        anchors = [AxiomaticAnchor(**a) for a in data.get("axiomatic_anchors", [])]
        commitments = [PlasticCommitment(**c) for c in data.get("plastic_commitments", [])]
        
        profile = KernelProfile(
            name=data["name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            axiomatic_anchors=anchors,
            plastic_commitments=commitments,
        )
        return cls(profile, is_readonly=data.get("is_readonly", False))

    def merge(self, other: ConstitutionalKernel) -> ConstitutionalKernel:
        """
        Merges another kernel into this one.
        The 'other' kernel's anchors and commitments are added as axiomatic anchors
        in the new merged kernel to ensure propagation constraints are non-negotiable.
        """
        new_anchors = list(self.anchors)
        
        # Add other's anchors
        for a in other.anchors:
            if not any(existing.id == a.id for existing in new_anchors):
                new_anchors.append(a)
        
        # Convert other's commitments to axiomatic anchors in the merged result
        for c in other.commitments:
            anchor_id = f"propagated_{c.id}"
            if not any(existing.id == anchor_id for existing in new_anchors):
                new_anchors.append(AxiomaticAnchor(
                    id=anchor_id,
                    description=f"{c.description} (Propagated: {c.current_value})",
                    keywords=c.keywords
                ))
        
        new_profile = KernelProfile(
            name=f"{self.name}_merged_{other.name}",
            version=self._profile.version,
            description=f"Merged kernel: {self.name} + {other.name}",
            axiomatic_anchors=new_anchors,
            plastic_commitments=list(self.commitments)
        )
        return ConstitutionalKernel(new_profile, is_readonly=self.is_readonly)

    def __repr__(self) -> str:
        return (
            f"ConstitutionalKernel(name={self.name!r}, "
            f"anchors={len(self.anchors)}, "
            f"commitments={len(self.commitments)})"
        )
