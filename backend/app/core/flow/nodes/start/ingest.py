from pathlib import Path

from sanic.log import logger

from app.core.flow.context import MANUAL_KEY, Context
from app.core.flow.fields.code import CodeField
from app.core.flow.handles import OutputHandle
from app.core.flow.nodes.base import CancellationSignal, Node, start_node
from app.models.flow import GraphCategory


@start_node(order=1, icon="boxMultipleSearch", categories=(GraphCategory.INGEST,))
class IngestStartNode(Node):
    example = CodeField(
        "request_example",
        language="jsonc",
        collapse=False,
        readonly=True,
        template="req/ingest.jsonc",
    )

    class Handles:
        output = OutputHandle()

    @classmethod
    async def execute(cls, *, context: Context, **kwargs):
        # skip check if it's a manual execution
        bootparams = context.bootparams
        if bootparams.get(MANUAL_KEY):
            return

        # check if NFO file already exists
        nfo_type = bootparams.get("nfo_type")
        nfo_path = bootparams.get("nfo_path")
        if nfo_type and nfo_path and Path(nfo_path).exists():
            logger.info("NFO file already exists, skipping generation: %s", nfo_path)
            raise CancellationSignal
