import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rioserver.library import Library  # noqa: E402
from tests.make_library import make  # noqa: E402


@pytest.fixture(scope="session")
def music_dir(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to generate test MP3s")
    d = tmp_path_factory.mktemp("music")
    make(d, seconds=1.0)
    return d


@pytest.fixture()
def library(music_dir, tmp_path) -> Library:
    lib = Library(music_dir, tmp_path / "library.db")
    lib.scan()
    return lib
