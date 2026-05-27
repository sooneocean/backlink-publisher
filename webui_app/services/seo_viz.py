from dataclasses import dataclass
from typing import Dict, Any
import json

@dataclass
class AnchorData:
    main_domain: str
    total_entries: int
    type_stats: Dict[str, Any]
    alarm: Dict[str, Any]

    @classmethod
    def from_report(cls, domain: str):
        # Funnel through PipelineAPI (Phase 2 Unit 4) instead of a raw
        # subprocess.run. report-anchors exits 6 on an anchor-distribution
        # alarm but still writes the JSON document to stdout, so the
        # capture-based report_anchors() keeps it. Only an empty stdout (a
        # genuine crash) is fatal — the old code did json.loads(stdout)
        # regardless of returncode and would raise an opaque JSONDecodeError.
        from ..api.pipeline_api import PipelineAPI

        result = PipelineAPI().report_anchors(domain)
        if not result.stdout.strip():
            raise RuntimeError(result.error or "report-anchors produced no output")
        data = json.loads(result.stdout)
        return cls(
            main_domain=data["main_domain"],
            total_entries=data["total_entries"],
            type_stats=data["type_stats"],
            alarm=data.get("alarm", {})
        )

    def to_chart_data(self):
        # Transform for e.g. ECharts
        labels = list(self.type_stats.keys())
        counts = [s["count"] for s in self.type_stats.values()]
        return {
            "labels": labels,
            "datasets": [{"label": "锚点分布", "data": counts}]
        }
