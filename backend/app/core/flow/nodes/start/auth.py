from app.core.flow.fields import CodeField
from app.core.flow.handles import OutputHandle
from app.core.flow.nodes.base import Node, start_node
from app.models.flow import GraphCategory

AUTH_CONFIG = """
login:
  mode: password # password | captcha | qrcode
  required: false

cookie:
  domain:
  path: /
  name:
""".lstrip()


@start_node(order=1, icon="key", categories=(GraphCategory.INDEXER,))
class AuthStartNode(Node):
    example = CodeField(
        "request_example",
        language="jsonc",
        collapse=True,
        readonly=True,
        template="req/auth.jsonc",
    )
    config = CodeField(
        "config",
        language="yaml",
        darkmode=True,
        default=AUTH_CONFIG,
    )

    class Handles:
        output = OutputHandle(tag="auth")
