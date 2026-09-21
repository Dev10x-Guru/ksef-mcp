import subprocess
import sys


def test_the_ksef_sdk_is_not_imported_at_startup() -> None:
    # Run out of process: the suite imports `ksef_port` at the top of other
    # modules, so in-process the module is always loaded and the guarantee
    # cannot be observed. Putting the import back on the CLI would otherwise
    # stay green and quietly undo it.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ksef_mcp.cli, sys; sys.exit(1 if 'ksef2' in sys.modules else 0)",
        ],
        check=False,
    )

    assert completed.returncode == 0
