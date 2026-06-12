import inspect
import json
import sys


class FastMCP:
    def __init__(self, name):
        self.name = name
        self._tools = {}

    def tool(self):
        def decorator(func):
            self._tools[func.__name__] = func
            return func

        return decorator

    def run(self, transport="stdio"):
        if transport != "stdio":
            raise ValueError("Only stdio transport is supported by the local MCP shim")

        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                response = self._handle(request)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(exc)},
                }

            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

    def _handle(self, request):
        method = request.get("method")
        request_id = request.get("id")

        if request_id is None:
            return None

        try:
            if method == "initialize":
                return self._result(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.name, "version": "1.0.0"},
                    },
                )

            if method == "tools/list":
                return self._result(
                    request_id,
                    {"tools": [self._tool_schema(name, func) for name, func in self._tools.items()]},
                )

            if method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name not in self._tools:
                    raise ValueError("Unknown tool: {}".format(name))

                result = self._tools[name](**arguments)
                return self._result(
                    request_id,
                    {"content": [{"type": "text", "text": str(result)}], "isError": False},
                )

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found: {}".format(method)},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(exc)},
            }

    def _result(self, request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _tool_schema(self, name, func):
        signature = inspect.signature(func)
        properties = {}
        required = []

        for param_name, param in signature.parameters.items():
            properties[param_name] = {"type": self._json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required

        return {
            "name": name,
            "description": inspect.getdoc(func) or "",
            "inputSchema": schema,
        }

    def _json_type(self, annotation):
        if annotation is int:
            return "integer"
        if annotation is float:
            return "number"
        if annotation is bool:
            return "boolean"
        return "string"
