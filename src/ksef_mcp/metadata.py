from importlib.metadata import version
from typing import Final

SERVER_NAME: Final[str] = "ksef-mcp"

VERSION: Final[str] = version(SERVER_NAME)
