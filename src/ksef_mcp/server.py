from mcp.server import MCPServer
from pydantic import BaseModel

from ksef_mcp import config, token_store
from ksef_mcp.metadata import SERVER_NAME, VERSION
from ksef_mcp.sync_store import SyncStore
from ksef_mcp.synchronisation import SynchronisationReport, Synchroniser

server: MCPServer = MCPServer(name=SERVER_NAME, version=VERSION)


class ServerInfo(BaseModel):
    name: str
    version: str


class SubjectTypeResult(BaseModel):
    subject_type: str
    outcome: str
    detail: str
    invoice_count: int
    part_count: int
    synchronised_up_to: str | None


class SynchronisationResult(BaseModel):
    environment: str
    subject_types: list[SubjectTypeResult]
    pending_exports: list[str]
    state_file: str


class NotConfigured(RuntimeError):
    pass


@server.tool()
def server_info() -> ServerInfo:
    """Report the name and version of the running KSeF connector."""
    return ServerInfo(name=SERVER_NAME, version=VERSION)


def describe(
    report: SynchronisationReport, *, environment: config.KsefEnvironment
) -> SynchronisationResult:
    return SynchronisationResult(
        environment=str(environment),
        subject_types=[
            SubjectTypeResult(
                subject_type=str(direction.direction),
                outcome=str(direction.outcome),
                detail=direction.detail,
                invoice_count=direction.invoice_count,
                part_count=direction.part_count,
                synchronised_up_to=(
                    None if direction.reached is None else direction.reached.isoformat()
                ),
            )
            for direction in report.directions
        ],
        pending_exports=list(report.pending_exports),
        state_file=report.state_path,
    )


def synchronise() -> SynchronisationResult:
    # Imported here rather than at module scope: `ksef2` pulls lxml, signxml and
    # xsdata, and a client listing tools must not pay half a second for a
    # dependency only this tool reaches for.
    from ksef_mcp.ksef_port.adapter import Ksef2Port

    configuration = config.load_configuration()
    if configuration is None:
        raise NotConfigured("Brak konfiguracji. Uruchom najpierw: ksef-mcp onboarding")
    stored = token_store.read_token(nip=configuration.nip)
    if stored is None:
        raise NotConfigured(
            f"Brak tokenu dla {configuration.nip}. "
            f"Zapisz go: ksef-mcp token set --nip {configuration.nip}"
        )
    synchroniser = Synchroniser(
        port=Ksef2Port(environment=configuration.environment),
        store=SyncStore(nip=configuration.nip, environment=configuration.environment),
    )
    return describe(
        synchroniser.run(nip=configuration.nip, token=stored.value),
        environment=configuration.environment,
    )


@server.tool()
def synchronise_invoices() -> SynchronisationResult:
    """Fetch every invoice package KSeF has finished since the last run.

    Takes no arguments on purpose. The date window, the package size and how
    many packages to ask for are decided by KSeF and by the hourly allowance,
    never by the caller: an agent driving them spends a twenty-per-hour budget
    in two minutes and the Ministry reads the pattern as working around a limit.

    Safe to call again. A package still being built stays recorded on disk with
    its key, so a second call continues it instead of asking for it twice.
    """
    return synchronise()


def main() -> None:
    server.run()
