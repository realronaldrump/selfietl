from selfietl.api.system import default_source_folder
from selfietl.config import load_config


def test_default_source_folder_is_created(tmp_path):
    config = load_config(tmp_path / "home")
    path = default_source_folder(config)

    assert path.exists()
    assert path.is_dir()
    assert path.name == "inbox"
