from __future__ import annotations

from pathlib import Path

from gpr_lab_pro.io.importer import DataImportParameters, GPRDataImporter, ImportedGPRData


class DatImporterService:
    def __init__(self) -> None:
        self._importer = GPRDataImporter()

    def import_file(
        self,
        path: str | Path,
        params: DataImportParameters | None = None,
        progress_callback=None,
        cancel_callback=None,
    ) -> ImportedGPRData:
        return self._importer.import_file(
            path,
            params or DataImportParameters(),
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
