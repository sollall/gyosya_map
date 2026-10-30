import datetime as dt

import pytest

from gyosya_map import db

TODAY = dt.date(2026, 10, 30)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.init_schema(c)
    db.load_seed(c)
    yield c
    c.close()


@pytest.fixture
def raw_dir(tmp_path):
    return tmp_path / "raw"
