from pathlib import Path

import pytest

from ebook_toolbox.resources import resource_path


@pytest.mark.parametrize("name", ["app_icon.ico", "app_icon.svg"])
def test_application_resource_exists(name: str) -> None:
    path = resource_path(name)

    assert path.is_file()
    assert path.parent.name == "resources"


@pytest.mark.parametrize("name", ["../app_icon.ico", "nested/app_icon.ico"])
def test_resource_path_rejects_directory_components(name: str) -> None:
    with pytest.raises(ValueError, match="must not contain directories"):
        resource_path(name)


def test_windows_icon_is_nonempty() -> None:
    icon_path = resource_path("app_icon.ico")

    assert isinstance(icon_path, Path)
    assert icon_path.stat().st_size > 0
