from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str


TOOL_DEFINITIONS = [
    ToolDefinition(
        name="calculator",
        label="Calculator",
        description="Evaluate simple arithmetic expressions.",
    ),
    ToolDefinition(
        name="current_time",
        label="Current Time",
        description="Return current server time.",
    ),
    ToolDefinition(
        name="query_order",
        label="Mock Order Query",
        description="Query mock ecommerce order status by order id.",
    ),
    ToolDefinition(
        name="mock_weather",
        label="Mock Weather",
        description="Return mock weather for a city.",
    ),
]


def list_tools() -> list[ToolDefinition]:
    return TOOL_DEFINITIONS


def run_tool(name: str, arguments: dict) -> dict:
    if name == "calculator":
        expression = str(arguments.get("expression", ""))
        allowed = set("0123456789+-*/(). ")
        if not expression or any(ch not in allowed for ch in expression):
            return {"error": "Only simple arithmetic expressions are allowed."}
        return {"result": eval(expression, {"__builtins__": {}}, {})}

    if name == "current_time":
        return {"result": datetime.now().isoformat(timespec="seconds")}

    if name == "query_order":
        order_id = str(arguments.get("order_id", "")).strip()
        orders = {
            "10086": "订单 10086 已发货，当前在上海转运中心，预计明天送达。",
            "10010": "订单 10010 待出库，预计今晚发货。",
            "12345": "订单 12345 已签收，签收人为本人。",
        }
        return {"order_id": order_id, "status": orders.get(order_id, "未找到该订单。")}

    if name == "mock_weather":
        city = str(arguments.get("city", "上海")).strip()
        return {"city": city, "weather": f"{city} 今天多云，气温 22-27 摄氏度。"}

    return {"error": f"Unknown tool: {name}"}
