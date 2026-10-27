"""Undo history for the tracklist editor."""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
from typing import Hashable

from .model import Album, Session, Track


@dataclass
class Snapshot:
    tracks: list[Track]
    album: Album
    sel: int            # selected row, restored with the state
    what: str           # i18n key naming the operation


class History:
    """Up to `limit` undoable operations.

    record() is called *before* the session changes. Calls sharing the same `group`
    (nudging one cut several times, typing in one field) form a single step until
    another operation, or a change of selection, closes the group.
    """

    def __init__(self, limit: int = 5) -> None:
        self._steps: deque[Snapshot] = deque(maxlen=limit)
        self._group: Hashable | None = None

    def record(self, session: Session, sel: int, what: str, group: Hashable | None = None) -> None:
        if group is not None and group == self._group:
            return
        self._steps.append(Snapshot(copy.deepcopy(session.tracks), copy.deepcopy(session.album), sel, what))
        self._group = group

    def close_group(self) -> None:
        self._group = None

    def undo(self, session: Session) -> Snapshot | None:
        self._group = None
        if not self._steps:
            return None
        snap = self._steps.pop()
        session.tracks = snap.tracks
        session.album = snap.album
        return snap

    def __len__(self) -> int:
        return len(self._steps)
