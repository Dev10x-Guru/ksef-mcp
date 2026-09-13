from importlib.metadata import version
from typing import Final

# The MCP server name is protocol identity that clients key on; the
# distribution name is packaging. importlib.metadata resolves the latter,
# so conflating them breaks the version lookup whenever either is renamed.
SERVER_NAME: Final[str] = "ksef-mcp"

DISTRIBUTION_NAME: Final[str] = "ksef-dev10x-guru"

VERSION: Final[str] = version(DISTRIBUTION_NAME)
