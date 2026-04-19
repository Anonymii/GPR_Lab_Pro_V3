from __future__ import annotations

from gpr_lab_pro.domain.models.results import ResultSnapshot


class SnapshotCacheManager:
    def __init__(self) -> None:
        self._cache: dict[str, ResultSnapshot] = {}

    def store(self, snapshot: ResultSnapshot) -> None:
        self._cache[snapshot.snapshot_id] = snapshot

    def store_many(self, snapshots: list[ResultSnapshot]) -> None:
        for snapshot in snapshots:
            self.store(snapshot)

    def get(self, snapshot_id: str) -> ResultSnapshot | None:
        return self._cache.get(snapshot_id)

    def clear(self) -> None:
        self._cache.clear()
