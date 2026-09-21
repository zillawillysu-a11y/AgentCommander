import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_mcp_discovery():
    async def check():
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__))
        params = StdioServerParameters(command=sys.executable, args=["-m", "commander"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"delegate_pi", "get_task_status", "get_task_result", "get_task_diagnostics", "verify_task", "list_tasks", "wait_for_task", "get_project_handoff", "export_project_handoff", "list_completion_notifications", "acknowledge_completion_notification"} <= names

    asyncio.run(check())
