"""Scaffold tools (SPEC P10): workspace/reference file tools, scheduling, status, end_session.

All strings the model can see — tool names, descriptions, results, error
messages — are mechanism-only: no budget, spend, horizon, or cap
information (I1), no strategy or memory-structure hints (I3).

The surface is **profile-selected** from 0.5.0 (SPEC P10, D3): the base
tools below are present for every run, and a profile at or above
``search`` adds ``search_reference``. Profile-added definitions are
appended AFTER the base ones, so the base surface's serialization — and
therefore ``session_start.tools_hash`` for a profile that adds nothing —
is unchanged from 0.4.0 at the same harness pin.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kami_agent.adapters.base import ToolDef
from kami_agent.journal import JOURNAL_ROOT
from kami_agent.tools.errors import ToolError
from kami_agent.tools.sandbox import SandboxError, resolve_path
from kami_agent.tools.search import ReferenceIndex, clamp_k

DEFAULT_WORKSPACE_QUOTA_BYTES = 10 * 1024 * 1024
DEFAULT_WAKE_MIN_MINUTES = 5.0
DEFAULT_WAKE_MAX_MINUTES = 24 * 60.0

# Upper bound on one `wait` call (caps.wait_max_seconds, SPEC P5, P10).
# Sized as a COOLDOWN primitive and nothing larger: the world's kami
# cooldowns run to roughly three minutes, so five minutes covers one with
# margin while staying far below any plausible session length. Waiting
# BETWEEN sessions is set_next_wake's job and always was.
DEFAULT_WAIT_MAX_SECONDS = 300.0

# How much of workspace/plan.md the session-start injection shows on the
# `planning` profile (SPEC P1.12.3, P13). A code constant rather than a
# manifest knob on purpose: prompts/planning.txt STATES this number, and a
# frozen asset cannot state a number an operator is free to change. Pinned
# to the asset by a unit test, exactly as the wake bounds above are (I5).
PLAN_FILE_MAX_BYTES = 8192

# --- scaffold profiles (SPEC D3) ----------------------------------------------
#
# The knowledge-delivery ladder, cumulative and in order: each profile is
# everything to its left plus its own rung. One agent version serves every
# arm of the family; the profile is a manifest key, not a branch.
#
# `pushed` adds nothing agent-side — its rung is result enrichment behind
# the lens's and harness's own runtime flags — but it sits above `search`,
# so it carries the search tool like every profile above that rung.
PROFILE_CONTROL = "control"
PROFILE_ORIENTATION = "orientation"
PROFILE_SEARCH = "search"
PROFILE_PUSHED = "pushed"
PROFILE_PLANNING = "planning"

PROFILES: tuple[str, ...] = (
    PROFILE_CONTROL,
    PROFILE_ORIENTATION,
    PROFILE_SEARCH,
    PROFILE_PUSHED,
    PROFILE_PLANNING,
)

DEFAULT_PROFILE = PROFILE_CONTROL


def profile_at_least(profile: str, rung: str) -> bool:
    """Is ``profile`` at or above ``rung`` on the cumulative ladder?"""
    return PROFILES.index(profile) >= PROFILES.index(rung)


SCAFFOLD_TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        name="workspace_write",
        description=(
            "Write a file under workspace/. Creates parent directories and "
            "replaces the whole file. Paths are relative to the workspace root."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDef(
        name="workspace_read",
        description=(
            "Read a file under workspace/ or reference/. Optional offset and "
            "length select a byte range. Paths are relative to the workspace "
            "root."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer"},
                "length": {"type": "integer"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="workspace_list",
        description=(
            "List files and sizes. Without a path, lists all of workspace/ and "
            "a one-line summary of reference/. With a path, lists that file or "
            "subtree. Paths are relative to the workspace root."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    ToolDef(
        name="workspace_delete",
        description=("Delete a file under workspace/. Paths are relative to the workspace root."),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolDef(
        name="set_next_wake",
        description=(
            "Choose when the next session starts, in minutes from now. Values "
            "outside the allowed range are clamped. The last call in a session "
            "wins."
        ),
        input_schema={
            "type": "object",
            "properties": {"minutes_from_now": {"type": "number"}},
            "required": ["minutes_from_now"],
        },
    ),
    ToolDef(
        name="get_status",
        description=("Return the current UTC time, the session number, and workspace usage."),
        input_schema={"type": "object", "properties": {}},
    ),
    ToolDef(
        name="end_session",
        description="End this session.",
        input_schema={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    ),
    # Mechanism only, on the search_reference / set_next_wake pattern: what
    # it does and that values outside the range are clamped — never a word
    # about when passing time is worth doing (I3). The clamp is stated for
    # the same reason set_next_wake states its own: a bound the agent can
    # observe by hitting it is better named than discovered.
    ToolDef(
        name="wait",
        description=(
            "Pause for a number of seconds, then return. Nothing else happens "
            "during the pause. Values outside the allowed range are clamped."
        ),
        input_schema={
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    ),
]

SCAFFOLD_TOOL_NAMES = frozenset(tool.name for tool in SCAFFOLD_TOOL_DEFS)

# Added by profiles at or above `search` (SPEC P10). Mechanism only: what
# it searches, what it returns, and how to expand a hit — never a word
# about when searching is worth doing (I3).
SEARCH_TOOL_DEF = ToolDef(
    name="search_reference",
    description=(
        "Keyword search over the read-only reference/ tree. Returns the "
        "passages that best match a query, each with its file path, byte "
        "offset and byte length, so workspace_read with that path, offset "
        "and length re-reads the passage in place. Optional k sets how many "
        "passages come back (default 5, at most 10)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer"},
        },
        "required": ["query"],
    },
)

# Every tool any profile can add. The harness/scaffold name-collision
# check runs against the UNION (base + all profile-added), so a mis-pinned
# harness is refused identically on every arm rather than only on the arms
# whose profile happens to carry the name.
PROFILE_TOOL_DEFS: dict[str, list[ToolDef]] = {PROFILE_SEARCH: [SEARCH_TOOL_DEF]}

ALL_SCAFFOLD_TOOL_NAMES = SCAFFOLD_TOOL_NAMES | {
    tool.name for defs in PROFILE_TOOL_DEFS.values() for tool in defs
}

# The empty-index answer: an explicit statement, not an empty list that
# reads as "nothing matched".
NO_REFERENCE_FILES = "no reference files"


def scaffold_tool_defs(profile: str = DEFAULT_PROFILE) -> list[ToolDef]:
    """The scaffold surface for one profile: base defs, then added defs.

    Order is the P10 ordering rule and it is load-bearing: ``tools_hash``
    hashes the list in order, so appending profile additions keeps the
    base surface's bytes — and a no-addition profile's hash — identical
    to 0.4.0's.
    """
    defs = list(SCAFFOLD_TOOL_DEFS)
    for rung, added in PROFILE_TOOL_DEFS.items():
        if profile_at_least(profile, rung):
            defs.extend(added)
    return defs


def scaffold_tool_names(profile: str = DEFAULT_PROFILE) -> frozenset[str]:
    """The names executable under one profile (the surface, and only it)."""
    return frozenset(tool.name for tool in scaffold_tool_defs(profile))


class ScaffoldTools:
    """Executes the scaffold tools against a run directory.

    One instance per session. ``set_next_wake`` and ``end_session``
    accumulate their effect on the instance (``requested_wake_min`` /
    ``clamped_wake_min`` / ``session_ended`` / ``end_reason``) for the
    runner to apply. ``emit`` receives the SPEC P9 ``workspace_write`` /
    ``workspace_delete`` telemetry payloads.

    ``profile`` selects the surface (P10): ``tool_defs`` / ``tool_names``
    are this session's, and a name outside them is not executable, so a
    profile that was not shown a tool cannot reach it by guessing.
    """

    def __init__(
        self,
        run_dir: str | Path,
        *,
        session_number: int = 0,
        profile: str = DEFAULT_PROFILE,
        workspace_quota_bytes: int = DEFAULT_WORKSPACE_QUOTA_BYTES,
        wake_min_minutes: float = DEFAULT_WAKE_MIN_MINUTES,
        wake_max_minutes: float = DEFAULT_WAKE_MAX_MINUTES,
        wait_max_seconds: float = DEFAULT_WAIT_MAX_SECONDS,
        budget_visible: bool = False,
        budget_remaining_usd: float | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.session_number = session_number
        self.profile = profile
        self.tool_defs = scaffold_tool_defs(profile)
        self.tool_names = frozenset(tool.name for tool in self.tool_defs)
        self.workspace_quota_bytes = workspace_quota_bytes
        self.wake_min_minutes = wake_min_minutes
        self.wake_max_minutes = wake_max_minutes
        self.wait_max_seconds = wait_max_seconds
        # Mechanism for a future budget-visible configuration; pinned False —
        # enabling it is a deliberate code change, not a config flip (X10, I1).
        self.budget_visible = budget_visible
        self.budget_remaining_usd = budget_remaining_usd
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._emit = emit or (lambda event, fields: None)

        self.workspace_root = self.run_dir / "workspace"
        self.reference_root = self.run_dir / "reference"
        # Scaffold-owned, read-only to the agent (P15). Not created here:
        # it appears when the first session is journaled, and until then
        # the file index says so rather than showing an empty tree.
        self.journal_root = self.run_dir / JOURNAL_ROOT
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        self.requested_wake_min: float | None = None
        self.clamped_wake_min: float | None = None
        self.session_ended = False
        self.end_reason: str | None = None
        # Built lazily on the first search of a session and reused for the
        # rest of it: the reference tree is read-only (P11), so one build
        # per session is enough and none happens on a profile that never
        # searches.
        self._reference_index: ReferenceIndex | None = None
        # The query and hit count of the last search_reference call, for
        # the loop to record on that call's telemetry row (P9). Same
        # accumulate-on-the-instance pattern as the wake and end flags.
        self.last_search: tuple[str, int] | None = None
        # (requested, actual) seconds of the last wait call, for the same
        # reason and by the same mechanism (P9): waiting must be
        # analyzable, and only the tool knows what it was asked for.
        self.last_wait: tuple[float, float] | None = None

    def execute(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.tool_names:
            raise ToolError(f"unknown tool: {name}")
        handler = getattr(self, name)
        try:
            return handler(**args)
        except TypeError as exc:
            raise ToolError(f"invalid arguments for {name}: {exc}") from exc

    # --- file tools ---------------------------------------------------------

    def workspace_write(self, path: str, content: str) -> str:
        resolved, root = resolve_path(self.run_dir, path)
        if root != "workspace":
            raise ToolError(
                "workspace_write only writes under workspace/; "
                "reference/ and journal/ are read-only"
            )
        if resolved == self.workspace_root.resolve() or resolved.is_dir():
            raise ToolError(f"path is a directory: {path!r}")
        data = content.encode("utf-8")
        existing = resolved.stat().st_size if resolved.is_file() else 0
        projected = self.workspace_bytes_used() - existing + len(data)
        if projected > self.workspace_quota_bytes:
            raise ToolError(
                "write rejected: workspace quota exceeded "
                f"({projected} of {self.workspace_quota_bytes} bytes)"
            )
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(data)
        except OSError as exc:
            raise ToolError(f"cannot write {path!r}: {exc.strerror or exc}") from exc
        rel = self._rel(resolved)
        self._emit(
            "workspace_write",
            {"path": rel, "bytes": len(data), "workspace_total_bytes": self.workspace_bytes_used()},
        )
        return f"Wrote {len(data)} bytes to {rel}."

    def workspace_read(
        self, path: str, offset: int | None = None, length: int | None = None
    ) -> str:
        resolved, _root = resolve_path(self.run_dir, path)
        if not resolved.is_file():
            raise ToolError(f"no such file: {path!r}")
        if offset is not None and offset < 0:
            raise ToolError("offset must be >= 0")
        if length is not None and length < 0:
            raise ToolError("length must be >= 0")
        data = resolved.read_bytes()
        start = offset or 0
        end = None if length is None else start + length
        return data[start:end].decode("utf-8", errors="replace")

    def workspace_list(self, path: str | None = None) -> str:
        if path is None:
            lines = self._tree_lines(self.workspace_root, "workspace")
            if not lines:
                lines = ["workspace/ (empty)"]
            # The journal is listed as a file with its size, not collapsed
            # to a summary the way reference/ is: it is one small file, and
            # the index is the ONLY place it is named. Nothing anywhere
            # tells the agent to read it — a listed path with a byte count
            # is a fact, and advice is not the scaffold's to give (I3).
            lines.extend(self._journal_lines())
            lines.append(self._reference_summary())
            return "\n".join(lines)
        resolved, _root = resolve_path(self.run_dir, path)
        if resolved.is_file():
            return f"{self._rel(resolved)} {resolved.stat().st_size}"
        if not resolved.is_dir():
            raise ToolError(f"no such path: {path!r}")
        lines = self._tree_lines(resolved, self._rel(resolved))
        return "\n".join(lines) if lines else f"{self._rel(resolved)}/ (empty)"

    def workspace_delete(self, path: str) -> str:
        resolved, root = resolve_path(self.run_dir, path)
        if root != "workspace":
            raise ToolError(
                "workspace_delete only deletes under workspace/; "
                "reference/ and journal/ are read-only"
            )
        if resolved.is_dir():
            raise ToolError(f"path is a directory, not a file: {path!r}")
        if not resolved.is_file():
            raise ToolError(f"no such file: {path!r}")
        resolved.unlink()
        rel = self._rel(resolved)
        self._emit(
            "workspace_delete",
            {"path": rel, "workspace_total_bytes": self.workspace_bytes_used()},
        )
        return f"Deleted {rel}."

    def search_reference(self, query: str, k: int | None = None) -> str:
        """Top-k reference passages for a query (profiles ≥ search).

        Deterministic by construction: the index is a pure function of the
        tree's bytes and the ordering breaks ties on path then offset, so
        the same tree and query give byte-identical output. Each hit's
        offset and length are byte positions, which is exactly what
        ``workspace_read`` slices on.
        """
        if self._reference_index is None:
            self._reference_index = ReferenceIndex.build(self.reference_root)
        index = self._reference_index
        hits = index.search(str(query), clamp_k(k))
        self.last_search = (str(query), len(hits))
        if not index.chunks:
            return json.dumps({"hits": [], "message": NO_REFERENCE_FILES}, ensure_ascii=False)
        return json.dumps({"hits": hits}, ensure_ascii=False)

    # --- scheduling / status / termination -----------------------------------

    def set_next_wake(self, minutes_from_now: float) -> str:
        try:
            minutes = float(minutes_from_now)
        except (TypeError, ValueError) as exc:
            raise ToolError("minutes_from_now must be a number") from exc
        if math.isnan(minutes) or math.isinf(minutes):
            raise ToolError("minutes_from_now must be a finite number")
        clamped = min(max(minutes, self.wake_min_minutes), self.wake_max_minutes)
        self.requested_wake_min = minutes
        self.clamped_wake_min = clamped
        return f"Next session in {clamped:g} minutes."

    def wait(self, seconds: float) -> str:
        """Block for up to ``wait_max_seconds``, then return (SPEC P10).

        The scaffold's only in-session time primitive. It exists because
        the world has cooldowns of a couple of minutes and the agent had
        no way to pass wall time inside a session at all: the alternatives
        it was left with were filler calls and polling the same value
        until the repetition breaker ended the session — the breaker
        firing on the ONLY waiting strategy the scaffold offered.

        Clamped, not rejected, exactly as ``set_next_wake`` is, and the
        result names the seconds actually slept so a clamp is observable
        rather than silent. A negative request clamps to zero: "do not
        wait" is a coherent thing to have asked for.
        """
        try:
            requested = float(seconds)
        except (TypeError, ValueError) as exc:
            raise ToolError("seconds must be a number") from exc
        if math.isnan(requested) or math.isinf(requested):
            raise ToolError("seconds must be a finite number")
        clamped = min(max(requested, 0.0), self.wait_max_seconds)
        start = time.perf_counter()
        self._sleep(clamped)
        actual = time.perf_counter() - start
        self.last_wait = (requested, actual)
        return f"Waited {clamped:g} seconds."

    def get_status(self) -> str:
        # Exactly these fields and nothing else (I1): no budget, spend,
        # token counts, elapsed-run figures, or T_max.
        status: dict[str, Any] = {
            "current_time_utc": self._clock().isoformat(),
            "session_number": self.session_number,
            "workspace_bytes_used": self.workspace_bytes_used(),
            "workspace_quota_bytes": self.workspace_quota_bytes,
        }
        if self.budget_visible and self.budget_remaining_usd is not None:
            status["budget_remaining_usd"] = self.budget_remaining_usd
        return json.dumps(status)

    def end_session(self, reason: str) -> str:
        self.session_ended = True
        self.end_reason = str(reason)
        return "Session ended."

    # --- helpers -------------------------------------------------------------

    def workspace_bytes_used(self) -> int:
        return sum(f.stat().st_size for f in self.workspace_root.rglob("*") if f.is_file())

    def _rel(self, resolved: Path) -> str:
        return str(resolved.relative_to(self.run_dir.resolve()))

    def _tree_lines(self, root: Path, prefix: str) -> list[str]:
        if not root.is_dir():
            return []
        files = sorted(p for p in root.rglob("*") if p.is_file())
        return [f"{prefix}/{p.relative_to(root)} {p.stat().st_size}" for p in files]

    def _journal_lines(self) -> list[str]:
        lines = self._tree_lines(self.journal_root, JOURNAL_ROOT)
        return lines or [f"{JOURNAL_ROOT}/ 0 files, 0 bytes, read-only"]

    def resolve_rel(self, path: str) -> str | None:
        """Run-dir-relative form of an agent-supplied path, or None.

        What the sandbox actually resolved a path to, for telemetry
        (``tool_call.path_resolved``, P9): the raw argument is what the
        agent typed, which is not the same thing and must not be read as
        if it were. None when the path does not resolve at all.
        """
        try:
            resolved, _root = resolve_path(self.run_dir, path)
        except SandboxError:
            return None
        try:
            return self._rel(resolved)
        except ValueError:
            return None

    def _reference_summary(self) -> str:
        if not self.reference_root.is_dir():
            return "reference/ 0 files, 0 bytes, read-only"
        files = [p for p in self.reference_root.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        return f"reference/ {len(files)} files, {total} bytes, read-only"
