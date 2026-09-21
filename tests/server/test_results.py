import pytest

from ksef_mcp.server.results import (
    InvoiceListingResult,
    InvoiceReviewResult,
    RenderedInvoiceResult,
    StatementResult,
    SynchronisationResult,
    ToolResult,
)

# The client of these tools is a language model, so a status field spelled
# `detail` in one answer and `message` in the next is a contract it cannot read
# (GH-171). The parametrisation is the guard: a sixth tool added later either
# inherits the shape or fails here.
TOOL_RESULTS: tuple[type[ToolResult], ...] = (
    SynchronisationResult,
    InvoiceListingResult,
    StatementResult,
    InvoiceReviewResult,
    RenderedInvoiceResult,
)


@pytest.mark.parametrize("result", TOOL_RESULTS)
def test_every_tool_answer_carries_the_same_four_fields(result: type[ToolResult]) -> None:
    assert {"nip", "environment", "message", "warnings"} <= set(result.model_fields)
