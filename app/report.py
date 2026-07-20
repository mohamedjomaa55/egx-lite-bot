from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


class ReportExporter:
    """Export scan results to CSV, Excel, and JSON."""

    def __init__(self, report_dir: Path) -> None:
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def export(self, rows: list[dict[str, object]]) -> None:
        """Write the result set to the supported export formats."""
        df = pd.DataFrame(rows)
        if df.empty:
            return

        csv_path = self.report_dir / "egx_swing_scout_results.csv"
        excel_path = self.report_dir / "egx_swing_scout_results.xlsx"
        json_path = self.report_dir / "egx_swing_scout_results.json"

        df.to_csv(csv_path, index=False)
        df.to_excel(excel_path, index=False)
        json_path.write_text(
            json.dumps(df.to_dict(orient="records"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
