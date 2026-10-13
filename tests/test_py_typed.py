from pathlib import Path

import garmin_intervals_bridge


def test_pep561_py_typed_marker_present():
    pkg_dir = Path(garmin_intervals_bridge.__file__).resolve().parent
    py_typed = pkg_dir / "py.typed"
    assert py_typed.is_file(), "PEP 561 py.typed marker file must exist in package"
