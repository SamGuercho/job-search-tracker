"""Start the MCP server (stdio). MCP clients run this file; it works from any working directory.

Get the exact configuration for your machine with:  .venv/bin/python -m jobtracker mcp-config
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from jobtracker.mcp_server import main  # noqa: E402

main()
