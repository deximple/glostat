from __future__ import annotations

import functools
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, ParamSpec, TypeVar

import structlog

# E8 / INV-GS-023: every LLM call records prompt_versions[expert] = sha256.
# A verdict missing prompt_versions is rejected upstream (see core.types.Verdict.__post_init__).

log: Final = structlog.get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str           # e.g. "E_FUNDAMENTAL.system"
    version: str        # semver-ish, "1.0.0"
    template: str       # raw template, parameter placeholders intact
    sha256: str
    registered_at: datetime
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @classmethod
    def from_text(
        cls,
        *,
        name: str,
        version: str,
        template: str,
        metadata: Mapping[str, str] | None = None,
    ) -> PromptTemplate:
        digest = _sha256(template)
        return cls(
            name=name,
            version=version,
            template=template,
            sha256=digest,
            registered_at=datetime.now(tz=UTC),
            metadata=tuple(sorted((metadata or {}).items())),
        )

    def fingerprint(self) -> str:
        return f"{self.name}@{self.version}#{self.sha256[:12]}"


class PromptCollisionError(RuntimeError):
    """A name+version pair already exists with a different SHA — versions must be immutable."""


@dataclass(slots=True)
class PromptRegistry:
    persist_path: Path | None = None
    _by_id: dict[str, PromptTemplate] = field(default_factory=dict)
    _by_sha: dict[str, PromptTemplate] = field(default_factory=dict)

    def register(
        self,
        *,
        name: str,
        version: str,
        template: str,
        metadata: Mapping[str, str] | None = None,
    ) -> PromptTemplate:
        prompt = PromptTemplate.from_text(
            name=name, version=version, template=template, metadata=metadata
        )
        key = self._key(name, version)
        existing = self._by_id.get(key)
        if existing is not None:
            if existing.sha256 != prompt.sha256:
                raise PromptCollisionError(
                    f"prompt {name}@{version} already registered with different sha "
                    f"({existing.sha256} != {prompt.sha256}). Bump version."
                )
            return existing
        self._by_id[key] = prompt
        self._by_sha[prompt.sha256] = prompt
        log.info("prompt.registered", id=key, sha=prompt.sha256[:12])
        if self.persist_path:
            self._persist()
        return prompt

    def get(self, name: str, version: str) -> PromptTemplate:
        key = self._key(name, version)
        if key not in self._by_id:
            raise KeyError(f"unknown prompt: {key}")
        return self._by_id[key]

    def lookup_by_sha(self, sha256: str) -> PromptTemplate | None:
        return self._by_sha.get(sha256)

    def fingerprints_for(self, names_versions: Mapping[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, version in names_versions.items():
            template = self.get(name, version)
            result[name] = template.sha256
        return result

    def __len__(self) -> int:
        return len(self._by_id)

    @staticmethod
    def _key(name: str, version: str) -> str:
        return f"{name}@{version}"

    @classmethod
    def load(cls, path: Path | str) -> PromptRegistry:
        p = Path(path)
        registry = cls(persist_path=p)
        if not p.exists():
            return registry
        for entry in json.loads(p.read_text("utf-8")):
            template = PromptTemplate(
                name=entry["name"],
                version=entry["version"],
                template=entry["template"],
                sha256=entry["sha256"],
                registered_at=datetime.fromisoformat(entry["registered_at"]),
                metadata=tuple(tuple(item) for item in entry.get("metadata", [])),
            )
            registry._by_id[cls._key(template.name, template.version)] = template
            registry._by_sha[template.sha256] = template
        return registry

    def _persist(self) -> None:
        assert self.persist_path is not None
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "name": p.name,
                "version": p.version,
                "template": p.template,
                "sha256": p.sha256,
                "registered_at": p.registered_at.isoformat(),
                "metadata": list(p.metadata),
            }
            for p in self._by_id.values()
        ]
        tmp = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(self.persist_path)


def with_prompt_version(
    *,
    registry: PromptRegistry,
    expert: str,
    name: str,
    version: str,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    template = registry.get(name, version)

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                _stamp_prompt(kwargs, expert, template)
                return await fn(*args, **kwargs)  # type: ignore[misc]

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            _stamp_prompt(kwargs, expert, template)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _stamp_prompt(kwargs: dict[str, Any], expert: str, template: PromptTemplate) -> None:
    bag = kwargs.setdefault("prompt_versions", {})
    if not isinstance(bag, dict):
        raise TypeError("prompt_versions kwarg must be a dict (or omitted)")
    if expert in bag and bag[expert] != template.sha256:
        raise PromptCollisionError(
            f"prompt_versions[{expert!r}] already set to a different sha"
        )
    bag[expert] = template.sha256


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Convenience type for use across modules.
ExpertPromptMap = Mapping[str, str]
PromptCallable = Callable[..., Awaitable[Any]]
