import os

os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from x2_ultra.model.builder import BuildOptions, build_model  # noqa: E402


@pytest.fixture(scope="session")
def full_model():
    return build_model(BuildOptions(scene="vacia"))
