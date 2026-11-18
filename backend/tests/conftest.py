"""One throwaway SQLite database for the whole test session."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="taper-test-")
os.environ["TAPER_DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["DEMO_MODE"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret")
