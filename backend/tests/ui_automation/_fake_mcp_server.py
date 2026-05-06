"""极简 fake MCP server — 仅用于 Task 7.2 集成测试。

用 ``mcp.server.fastmcp`` 暴露两个伪 browser 工具：``browser_navigate``
和 ``browser_click``。stdio 模式启动，让 ``MCPBridge`` 真正走一遍
"npx 子进程 + handshake + list_tools + call_tool" 全链路。

这样我们既能在 CI 不依赖外网（不需要真的 ``npx @playwright/mcp``）跑端
到端测试，又能验证 MCP 协议层的字段映射没出问题。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-browser")


@mcp.tool(description="Open a URL in the fake browser")
def browser_navigate(url: str) -> str:
    """伪 navigate：返回 URL，方便测试断言。"""
    return f"navigated: {url}"


@mcp.tool(description="Click an element by selector")
def browser_click(selector: str, force: bool = False) -> str:
    """伪 click：返回拼接结果，验证 bool 默认参数也能透传。"""
    return f"clicked: {selector} force={force}"


if __name__ == "__main__":
    mcp.run("stdio")
