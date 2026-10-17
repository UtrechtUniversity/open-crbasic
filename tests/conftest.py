
import os


def pytest_sessionstart(session):
    os.environ["OPENCRBASIC_STRICT"] = "1"
