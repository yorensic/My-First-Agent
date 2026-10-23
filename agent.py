import os
import re
import subprocess

from openai import OpenAI

# ============================================================
# 1. 读取环境变量里的 API Key
# ============================================================
api_key = os.getenv("DEEPSEEK_API_KEY")

if not api_key:
    print("错误：没有找到环境变量 DEEPSEEK_API_KEY。")
    print("请先按照教程设置环境变量，再重新运行。")
    exit(1)

client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com",  # DeepSeek 用 OpenAI 兼容接口
)


# ============================================================
# 2. 危险命令拦截
# ============================================================
# 这些是“绝对不能让它执行”的命令模式
DANGER_PATTERNS = [
    r"rm\s+(-\w+\s+)*(/|~)",      # rm -rf /  或  rm -rf ~
    r"rm\s+-rf\s+\*",              # rm -rf *
    r"mkfs",                       # 格式化磁盘
    r"dd\s+if=",                   # dd 写磁盘
    r"sudo\s+",                    # sudo 提权
    r"shutdown",                   # 关机
    r"reboot",                     # 重启
    r":\(\)\s*\{\s*:\|:&\s*\};:", # fork 炸弹
    r"format\s+[a-zA-Z]:",         # Windows 格式化
    r"del\s+/[sfq]",               # Windows 强制删除
    r"rd\s+/s",                    # Windows 递归删目录
]


def is_dangerous(command: str) -> bool:
    """
    检查命令是否危险。
    如果匹配到任何一条危险模式，返回 True，表示“禁止执行”。
    """
    for pattern in DANGER_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


# ============================================================
# 3. 三个工具的具体实现
# ============================================================
def run_bash(command: str, timeout: int = 30) -> str:
    """
    执行一条 shell 命令，返回输出。
    执行之前先做危险命令检查。
    """
    if is_dangerous(command):
        return f"[已拦截] 该命令被判定为危险命令，禁止执行：{command}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        return output if output.strip() else "(命令执行成功，没有输出)"
    except subprocess.TimeoutExpired:
        return f"[超时] 命令执行超过 {timeout} 秒，已强制终止。"
    except Exception as e:
        return f"[执行出错] {e}"


def read_file(path: str) -> str:
    """读取文件内容。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[错误] 文件不存在：{path}"
    except Exception as e:
        return f"[读取出错] {e}"


def write_file(path: str, content: str) -> str:
    """把内容写入文件（会覆盖原内容）。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入文件：{path}，共 {len(content)} 个字符。"
    except Exception as e:
        return f"[写入出错] {e}"


# ============================================================
# 4. 告诉模型“有哪些工具可以用”
# ============================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "执行一条 shell 命令，返回命令的输出。适合查看目录、运行程序、执行脚本等。危险命令会被拦截。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文件的内容并返回。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入一个文件。如果文件已存在，会覆盖原内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


# 工具名 -> 真正的函数
TOOL_MAP = {
    "run_bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
}


# ============================================================
# 5. Agent Loop（核心循环）
# ============================================================
def agent_loop():
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个能使用工具的助手。"
                "你可以执行 shell 命令、读文件、写文件。"
                "在执行任何操作之前，先想清楚是否真的需要。"
                "如果不需要工具就能回答，就直接回答，不要调用工具。"
            ),
        }
    ]

    print("Agent 已启动。输入 exit 或 quit 退出。")
    print("-" * 50)

    while True:
        # ---------- 5.1 接收用户输入 ----------
        user_input = input("\n你 > ").strip()

        if user_input.lower() in ("exit", "quit"):
            print("再见。")
            break

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # ---------- 5.2 进入“模型 -> 工具 -> 模型”的内层循环 ----------
        while True:
            # 请求模型
            response = client.chat.completions.create(
                model="deepseek-flash",   # 也可以用 deepseek-chat
                messages=messages,
                tools=tools,
                extra_body={"thinking": {"type": "disabled"}},
            )

            msg = response.choices[0].message

            # 先把模型这条消息原样追加进历史（后面如果调工具必须这样做）
            messages.append(msg)

            tool_calls = msg.tool_calls

            # ---------- 5.3 如果没有工具调用，说明模型给出了最终回答 ----------
            if not tool_calls:
                print(f"\n助手 > {msg.content}")
                break

            # ---------- 5.4 有工具调用：逐个执行 ----------
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                raw_args = tool_call.function.arguments

                # 打印 Trace：调用了什么工具、参数是什么
                print(f"\n[Trace] 调用工具：{tool_name}")
                print(f"[Trace] 参数：{raw_args}")

                # 从 JSON 字符串解析参数
                import json
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    print(f"[Trace] 参数解析失败，跳过：{raw_args}")
                    tool_result = "参数不是合法的 JSON，无法执行。"
                else:
                    # 执行工具
                    func = TOOL_MAP.get(tool_name)
                    if func is None:
                        tool_result = f"未知工具：{tool_name}"
                    else:
                        tool_result = func(**args)

                # 打印工具执行结果（截断一点，免得刷屏）
                preview = tool_result[:500]
                if len(tool_result) > 500:
                    preview += "...（输出已截断）"
                print(f"[Trace] 结果：{preview}")

                # 把工具结果喂回给模型
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

            # 工具结果已经追加，内层循环继续，让模型根据结果继续说话或再调工具


# ============================================================
# 6. 启动
# ============================================================
if __name__ == "__main__":
    agent_loop()