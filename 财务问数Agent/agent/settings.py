import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    project_root: Path
    database: Path
    validation: Path
    api_key_file: Path
    base_url: str
    model: str
    business_year: int
    timeout: float
    max_rows: int

    @classmethod
    def load(cls):
        path = ROOT / "config.local.json"
        values = json.loads((path if path.exists() else ROOT / "config.example.json").read_text(encoding="utf-8-sig"))
        project = Path(values["project_root"]).resolve()
        key = Path(values["api_key_file"])
        return cls(project, project / values["database_file"], project / values["validation_file"],
                   key if key.is_absolute() else ROOT / key, values["base_url"], values["model"],
                   int(values["business_year"]), float(values["query_timeout_seconds"]), int(values["max_result_rows"]))

    def key(self):
        value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not value and self.api_key_file.exists():
            value = self.api_key_file.read_text(encoding="utf-8-sig").strip()
        if not value or value.startswith(("请", "YOUR_", "填写", "<")):
            return ""
        if any(c.isspace() for c in value):
            raise ValueError("Key文件只能包含一行API Key，不要填写说明文字。")
        return value

    def key_configured(self):
        """仅检测是否有环境配置/非空文件，不读取Key，不代表鉴权验证通过。"""
        return bool(os.environ.get('DEEPSEEK_API_KEY','').strip()) or (self.api_key_file.is_file() and self.api_key_file.stat().st_size>0)
