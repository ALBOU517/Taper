import pytest

from app import scenarios as S


@pytest.mark.parametrize("path", S.scenarios(), ids=lambda p: p.stem)
def test_scenario(path):
    r = S.run(path)
    failed = [f"{label}: {detail}" for label, ok, detail in r.checks if not ok]
    assert not failed, "\n".join(failed)
