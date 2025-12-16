import json
import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from .state import PlanState
from .tools import search_general_info, search_transport_ticket, search_social_reviews

load_dotenv()

# 配置 LLM (如果用 DeepSeek，模型名填 deepseek-chat)
llm = ChatOpenAI(model="deepseek-chat", temperature=0.7,base_url=os.getenv("OPENAI_BASE_URL"), openai_api_key=os.getenv("OPENAI_API_KEY"))

# --- Nodes (节点逻辑) ---

def node_collect_info(state: PlanState):
    """节点：信息收集"""
    last_msg = state["messages"][-1].content if state["messages"] else ""
    current_req = state.get("user_requirements", {})

    # 1. 使用 LLM 分析用户意图和提取信息
    extractor_prompt = ChatPromptTemplate.from_messages([
        ("system", """
        你是专业的旅游顾问。你的目标是收集以下关键信息：
        1. 目的地 (destination)
        2. 出发地 (origin)
        3. 游玩天数 (duration)
        4. 预算 (budget)
        
        当前已收集: {current_req}
        
        任务：
        1. 分析用户最新的回复，提取上述信息。
        2. 检查还缺少什么信息。
        3. 如果信息不全，生成一个友好的追问。
        4. 如果信息全了，设置 status 为 'researching'。
        
        请以 JSON 格式返回 (不要 markdown):
        {{
            "extracted": {{...}},
            "missing": ["..."],
            "reply": "...",
            "status": "collecting" 或 "researching"
        }}
        """),
        ("user", "{input}")
    ])
    
    chain = extractor_prompt | llm
    res = chain.invoke({"input": last_msg, "current_req": json.dumps(current_req, ensure_ascii=False)})
    
    # 解析 JSON (生产环境建议加 try-catch 和 json repair)
    try:
        data = json.loads(res.content.strip("```json").strip("```"))
    except:
        # Fallback if JSON fails
        return {"messages": [{"role": "assistant", "content": "抱歉，我没听清，请再说一遍您的需求？"}]}

    # 更新需求字典
    new_req = {**current_req, **data.get("extracted", {})}
    
    # 构造回复
    ai_msg = {"role": "assistant", "content": data["reply"]}
    if data["status"] == "researching":
        ai_msg["content"] += "\n(信息收集完毕，正在为您全网检索交通、住宿和避雷指南...)"
    
    return {
        "user_requirements": new_req,
        "messages": [ai_msg],
        "status": data["status"],
        "missing_fields": data.get("missing", [])
    }

def node_research(state: PlanState):
    """节点：联网调研"""
    req = state["user_requirements"]
    dest = req.get("destination")
    origin = req.get("origin")
    
    print(f"🔍 开始搜索: {dest} from {origin}")
    
    # 并行调用搜索工具
    transport = search_transport_ticket.invoke({"origin": origin, "destination": dest})
    attractions = search_general_info.invoke({"query": f"{dest} 必去景点 门票价格 2024"})
    social_reviews = search_social_reviews.invoke({"destination": dest})
    
    full_research = f"""
    【交通数据 (来源 12306/携程等)】:
    {transport}
    
    【景点官方数据】:
    {attractions}
    
    【社交媒体避雷 (来源 小红书/贴吧)】:
    {social_reviews}
    """
    
    return {
        "research_content": full_research,
        "status": "planning"
    }

def node_generate_plan(state: PlanState):
    """节点：生成初版计划"""
    req = state["user_requirements"]
    
    prompt = f"""
    基于以下真实调研数据，为用户生成一份 {req.get('duration')} 的 {req.get('destination')} 旅游计划。
    
    用户预算: {req.get('budget')}
    
    【调研数据】:
    {state['research_content']}
    
    【要求】:
    1. 结构清晰：包含【交通建议】、【住宿推荐】、【每日详细行程】、【费用预估】。
    2. **真实性检查**：如果社交媒体数据中有避雷信息（如某地坑人），必须在计划中高亮标注提醒用户！
    3. **引用来源**：文末必须列出【参考资料】，保留调研数据中的URL。
    4. 格式：使用 Markdown。
    """
    
    response = llm.invoke(prompt)
    return {
        "plan_draft": response.content,
        "messages": [{"role": "assistant", "content": "计划已生成！您可以在右侧查看详情。\n\n您对这个计划满意吗？如果需要调整（例如更宽松的行程、换个酒店），请直接告诉我。如果满意，请回复“确认”。"}],
        "status": "refining"
    }

def node_refine_plan(state: PlanState):
    """节点：修改或确认计划"""
    last_user_msg = state["messages"][-1].content
    
    # 简单的意图判断
    check_prompt = f"用户输入: '{last_user_msg}'。如果是确认/满意/没问题，返回 YES。如果是提出修改意见，返回 NO。"
    is_approved = llm.invoke(check_prompt).content.strip()
    
    if "YES" in is_approved:
        return {
            "status": "finished",
            "messages": [{"role": "assistant", "content": "太好了！最终计划已确认，正在为您生成可下载文件..."}]
        }
    
    # 执行修改
    refine_prompt = f"""
    原计划:
    {state['plan_draft']}
    
    用户修改意见:
    {last_user_msg}
    
    请根据意见修改计划，保持原有Markdown格式，保留参考资料。
    """
    new_plan = llm.invoke(refine_prompt).content
    
    return {
        "plan_draft": new_plan,
        "messages": [{"role": "assistant", "content": "已根据您的意见调整计划，请再次查阅。"}],
        "status": "refining"
    }

# --- 构建图 ---
workflow = StateGraph(PlanState)

workflow.add_node("collector", node_collect_info)
workflow.add_node("researcher", node_research)
workflow.add_node("planner", node_generate_plan)
workflow.add_node("refiner", node_refine_plan)

# 边逻辑
def route_after_collect(state):
    if state["status"] == "researching":
        return "researcher"
    return END # 等待用户下一次输入

def route_after_refine(state):
    if state["status"] == "finished":
        return END
    return END # 等待用户下一次反馈

workflow.add_edge(START, "collector")
workflow.add_conditional_edges("collector", route_after_collect)
workflow.add_edge("researcher", "planner")
workflow.add_edge("planner", END) # 这是一个断点，等待前端展示并获取用户反馈
# ✅ 必须使用 add_conditional_edges
workflow.add_conditional_edges("refiner", route_after_refine)

# 在 main.py 中调用时，如果是 refining 阶段，应该直接重入 refiner
# 为了简化，我们通常在 server 层处理重入逻辑，或者这里加个路由判断
# 修正：我们需要根据当前 step 决定入口，这里简化为通用图，通过 memory 记忆状态
app_graph = workflow.compile()