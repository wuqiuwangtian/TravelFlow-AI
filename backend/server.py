# --- 新增这两行 ---
from dotenv import load_dotenv
load_dotenv()  # 必须放在最前面，先加载环境变量！

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional  # <--- 必须导入这个
from dotenv import load_dotenv # <--- 记得之前加的这个要在最上面
from core.graph import app_graph
from core.pdf_gen import generate_pdf_file
import uuid
import os

load_dotenv()  # 必须放在最前面，先加载环境变量！

app = FastAPI()

# --- 2. 新增这行代码 ---
# 这行代码的意思是：允许外部通过 http://.../static/文件名 访问 backend/static 目录下的文件
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static") 
# -----------------------

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存存储会话状态 (生产环境请用 Redis)
threads_memory = {}

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    thread_id = req.thread_id or str(uuid.uuid4())
    
    # 获取或初始化状态
    current_state = threads_memory.get(thread_id, {
        "messages": [], 
        "user_requirements": {}, 
        "status": "collecting",
        "plan_draft": ""
    })
    
    # 注入用户消息
    current_state["messages"].append({"role": "user", "content": req.message})
    
    # 决定入口节点：如果已经在 refining 阶段，用户发消息应该进入 refiner
    # LangGraph 的 invoke 会自动根据状态流转，但我们需要正确处理输入
    # 这里简单处理：直接把 state 丢进去运行
    
    final_state = app_graph.invoke(current_state)
    
    # 更新内存
    threads_memory[thread_id] = final_state
    
 # ✅ 修正：先判断是对象还是字典，再取值
    last_msg = final_state["messages"][-1]
    
    if hasattr(last_msg, "content"):
        ai_reply = last_msg.content  # 如果是 AIMessage 对象
    else:
        ai_reply = last_msg["content"] # 如果是字典
    
    return {
        "thread_id": thread_id,
        "response": ai_reply,
        "plan": final_state.get("plan_draft", ""),
        "status": final_state.get("status")
    }

@app.get("/api/download/{thread_id}")
async def download_plan(thread_id: str):
    state = threads_memory.get(thread_id)
    if not state or not state.get("plan_draft"):
        return {"error": "Plan not ready"}
    
    # 生成 PDF (代码见下文)
    filename = generate_pdf_file(state["plan_draft"], thread_id)
    # 实际项目中应返回文件流或 CDN 链接
    return {"url": f"http://localhost:8000/static/{filename}", "message": "PDF generated (mock)"}