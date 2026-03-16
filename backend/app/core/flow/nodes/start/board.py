from app.core.flow.fields import CodeField
from app.core.flow.handles import OutputHandle
from app.core.flow.nodes.base import Node, start_node
from app.models.flow import GraphCategory

BOARD_CONFIG = """
display:
  view_modes:
    - table
    - grid
  cover_ratio: 16/9

calendar:
  week: false
  week_start: 0
""".lstrip()


@start_node(order=2, icon="appsListDetail", categories=(GraphCategory.INDEXER,))
class BoardStartNode(Node):
    config = CodeField(
        "config",
        language="yaml",
        darkmode=True,
        default=BOARD_CONFIG,
    )

    class Handles:
        output = OutputHandle(tag="board")
