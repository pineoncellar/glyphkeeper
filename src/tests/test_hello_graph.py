"""
@File     :   test_hello_graph.py
@Desc     :   验证 LangGraph Graph Runtime 环境可用性
@Note     :   测试 StateGraph 的基本构建、编译、执行全流程
"""

import pytest
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Optional


class TestState(TypedDict):
    """测试用状态类型"""
    __test__ = False                # pytest: 不要作为测试类收集
    input: str
    output: str


# ------- 辅助 Node -------

def uppercase_node(state: TestState) -> dict:
    """将输入文本转为大写"""
    return {"output": state["input"].upper()}


# ------- 测试用例 -------

def test_hello_graph():
    """验证最小 LangGraph 图可正常编译和执行"""
    builder = StateGraph(TestState)
    builder.add_node("upper", uppercase_node)
    builder.add_edge(START, "upper")
    builder.add_edge("upper", END)

    app = builder.compile()                             # 状态：编译图
    result = app.invoke({"input": "hello", "output": ""})  # 状态：执行图
    assert result["output"] == "HELLO"
    print("LangGraph环境正常")
