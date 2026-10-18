"""User-local preferences; never stores book paths or changes tool safety."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths


@dataclass(frozen=True)
class Preferences:
    theme: str = "light"
    remember_window: bool = False
    width: int = 1100
    height: int = 720


def default_preferences_path() -> Path:
    return (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
        / "settings.json"
    )


class PreferencesStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_preferences_path()
        self.warning = ""

    def load(self) -> Preferences:
        self.warning = ""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置不是 JSON 对象")
        except FileNotFoundError:
            return Preferences()
        except (OSError, UnicodeError, ValueError) as error:
            self.warning = f"设置读取失败，已使用默认值；原配置未改写：{error}"
            return Preferences()
        theme, remember = data.get("theme"), data.get("remember_window")
        width, height = data.get("width"), data.get("height")
        if not (
            theme in ("light", "dark")
            and type(remember) is bool
            and type(width) is int
            and 800 <= width <= 10000
            and type(height) is int
            and 600 <= height <= 10000
        ):
            self.warning = "设置包含无效值，已使用默认值；原配置未改写。"
            return Preferences()
        return Preferences(theme, remember, width, height)

    def save(self, value: Preferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(value), ensure_ascii=False, indent=2).encode("utf-8")
        output = QSaveFile(str(self.path))
        output.setDirectWriteFallback(False)
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
        self.warning = ""
