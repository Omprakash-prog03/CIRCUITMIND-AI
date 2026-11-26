import os

from .server import run

if __name__ == "__main__":
    run(os.environ.get("CIRCUITMIND_HOST", "127.0.0.1"), int(os.environ.get("CIRCUITMIND_PORT", "8000")))
