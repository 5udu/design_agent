"""One registration list supplies both model schemas and executable handlers."""
from app.tools.images import GENERATE_IMAGE, EDIT_IMAGE, INSPECT_IMAGE
from app.tools.project import READ_PROJECT
from app.tools.user import ASK_USER


REGISTRY = {tool.name: tool for tool in (
    READ_PROJECT, GENERATE_IMAGE, EDIT_IMAGE, INSPECT_IMAGE, ASK_USER)}
TOOL_SCHEMAS = [tool.schema() for tool in REGISTRY.values()]
