"""Run the LLM Wiki MCP server over stdio.

Run: uv run python scripts/mcp_server.py
"""

from llm_wiki.mcp_server import mcp

if __name__ == "__main__":
    mcp.run()
