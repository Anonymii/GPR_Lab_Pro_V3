from __future__ import annotations

from pathlib import Path

from gpr_lab_pro.io.importer import DataImportParameters, GPRDataImporter, ImportedGPRData
from gpr_lab_pro.io.importers.three_d_radar_importer import ThreeDRadarImporter


class DatImporterService:
    def __init__(self) -> None:
        self._importer = GPRDataImporter()
        self._three_d_radar_importer = ThreeDRadarImporter()

    def import_file(
        self,
        path: str | Path,
        params: DataImportParameters | None = None,
        progress_callback=None,
        cancel_callback=None,
    ) -> ImportedGPRData:
        path = Path(path)
        if path.suffix.lower() == ".3dra" or path.name.lower().endswith(".3dra.extracted"):
            return self._three_d_radar_importer.import_file(
                path,
                params or DataImportParameters(),
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        return self._importer.import_file(
            path,
            params or DataImportParameters(),
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
