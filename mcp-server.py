import time
import psutil
import json
import logging
from datetime import datetime, timedelta, timezone
from flask import Flask, Response, jsonify, request
from threading import Event
from uuid import uuid4

app = Flask(__name__)

# MCP Protocol Constants
MCP_VERSION = "1.0"
MCP_PROTOCOL = "streamable_http"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('mcp_server.log'),
        logging.StreamHandler()
    ]
)

def log_message(message):
    """统一的日志记录函数"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"[{timestamp}] {message}")

def get_cpu_utilization():
    """Get current CPU utilization percentages"""
    # Get per-core utilization
    per_cpu = psutil.cpu_percent(interval=1, percpu=True)
    # Get total utilization
    total = psutil.cpu_percent(interval=1)

    # 获取北京时间（UTC+8）
    beijing_time = datetime.now(timezone.utc) + timedelta(hours=8)
    readable_time = beijing_time.strftime('%Y-%m-%d %H:%M:%S')

    result = {
        "total": total,
        "per_cpu": per_cpu,
        "timestamp": readable_time
    }
    log_message(f"CPU Utilization: {result}")
    return result

@app.route('/mcp', methods=['POST'])
def mcp_endpoint():
    # Generate session ID for streaming
    session_id = request.headers.get('Mcp-Session-Id', str(uuid4()))
    
    # Log request
    log_message(f"Received request | Session: {session_id}")
    log_message(f"Headers: {dict(request.headers)}")
    log_message(f"Raw data: {request.data.decode('utf-8') if request.data else 'Empty'}")

    # Protocol validation
    if request.headers.get('X-Mcp-Protocol') != MCP_PROTOCOL:
        error_msg = "Unsupported protocol"
        log_message(f"Error: {error_msg}")
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": error_msg}
        }), 400
    
    if request.headers.get('X-Mcp-Version') != MCP_VERSION:
        error_msg = "Unsupported version"
        log_message(f"Error: {error_msg}")
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": error_msg}
        }), 400

    try:
        request_data = request.get_json()
        if not request_data:
            error_msg = "Empty request data"
            log_message(f"Error: {error_msg}")
            return jsonify({
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": error_msg}
            }), 400

        # Handle JSON-RPC methods
        method = request_data.get("method")
        if not method:
            error_msg = "Missing method in request"
            log_message(f"Error: {error_msg}")
            return jsonify({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": error_msg}
            }), 400

        log_message(f"Processing method: {method}")

        # Method routing
        if method == "initialize":
            return handle_initialize(request_data)
        elif method == "notifications/initialized":
            return handle_notifications_initialized(request_data)
        elif method == "tools/list":
            return handle_tools_list(request_data)
        elif method == "tools/call":
            return handle_tools_call(request_data, session_id)
        else:
            error_msg = f"Unknown method: {method}"
            log_message(f"Error: {error_msg}")
            return jsonify({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": error_msg}
            }), 400

    except Exception as e:
        error_msg = f"Internal error: {str(e)}"
        log_message(f"Exception: {error_msg}")
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": error_msg}
        }), 500

def handle_initialize(request_data):
    """Handle initialize request"""
    response = {
        "jsonrpc": "2.0",
        "id": request_data.get("id", 0),
        "result": {
            "capabilities": {
                "toolUse": True,
                "streaming": True,
                "supportedMethods": ["tools/list", "tools/call"]
            },
            "serverInfo": {
                "name": "Linux CPU Monitor",
                "version": "1.0.0"
            }
        }
    }
    log_message(f"Initialize response: {json.dumps(response, indent=2)}")
    return jsonify(response)

def handle_notifications_initialized(request_data):
    """Handle initialized notification"""
    response = {
        "jsonrpc": "2.0",
        "id": request_data.get("id"),
        "result": {
            "status": "ready",
            "capabilities": {
                "notificationTypes": ["log", "status"]
            }
        }
    }
    log_message(f"Initialized notification response: {response}")
    return jsonify(response)

def handle_tools_list(request_data):
    """Handle tools/list request"""
    tools_list = {
        "jsonrpc": "2.0",
        "id": request_data.get("id", 1),
        "result": {
            "tools": [{
                "provider_name": "linux_monitor",
                "name": "get_cpu_utilization",
                "description": "Get current CPU utilization metrics",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "interval": {
                            "type": "number",
                            "description": "Sampling interval in seconds",
                            "default": 1
                        }
                    },
                    "required": []
                },
                "enabled": True
            }]
        }
    }
    log_message(f"Tools list response: {json.dumps(tools_list, indent=2)}")
    return jsonify(tools_list)

def handle_tools_call(message: dict, session_id: str):
    """Handle tools/call request with single SSE response"""

    # 验证是否包含 id 字段
    request_id = message.get("id")
    if not request_id:
        error_msg = "Tools call must include an id"
        log_message(f"Error: {error_msg}")
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": error_msg}
        }), 400

    # 验证工具名称
    params = message.get("params", {})
    tool_name = params.get("name")
    if tool_name != 'get_cpu_utilization':
        error_msg = f"Unknown tool: {tool_name}"
        log_message(f"Error: {error_msg}")
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": error_msg}
        }), 400

    # 获取 CPU 数据
    cpu_util = get_cpu_utilization()

    # 构造 SSE 数据（一次发送后关闭）
    def generate():
        sse_data = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"cpu_utilization": cpu_util}]}
        }
        yield f"data: {json.dumps(sse_data)}\n\n"
        log_message("Sent SSE response and closed stream.")

    return Response(generate(), content_type='text/event-stream')

if __name__ == '__main__':
    log_message("Starting MCP Server...")
    log_message(f"Protocol: {MCP_PROTOCOL}, Version: {MCP_VERSION}")
    app.run(host='0.0.0.0', port=5000, threaded=True)
