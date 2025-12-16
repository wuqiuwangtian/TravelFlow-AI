import os
from tavily import TavilyClient
from langchain_core.tools import tool

# 初始化 Tavily
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

def _search(query: str, max_results=3) -> str:
    """基础搜索函数"""
    try:
        response = tavily.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            include_answer=True
        )
        content = ""
        for res in response['results']:
            content += f"SOURCE: [{res['title']}]({res['url']})\nCONTENT: {res['content']}\n\n"
        return content
    except Exception as e:
        return f"Search Error: {str(e)}"

@tool
def search_general_info(query: str):
    """搜索通用旅游信息，如景点介绍、官方通告"""
    return _search(query)

@tool
def search_transport_ticket(origin: str, destination: str):
    """
    专门搜索交通信息。
    强制包含 12306 和 携程/去哪儿 等关键词，确保票务信息真实。
    """
    query = f"{origin} 到 {destination} 火车票 高铁票 飞机票 价格 时刻表 2024 site:12306.cn OR site:ctrip.com OR site:qunar.com"
    return _search(query, max_results=4)

@tool
def search_social_reviews(destination: str):
    """
    专门搜索社交媒体评价（避雷、真实体验）。
    定向搜索小红书、知乎、贴吧。
    """
    query = f"{destination} 旅游 避雷 坑 真实评价 攻略 site:xiaohongshu.com OR site:zhihu.com OR site:tieba.baidu.com"
    return _search(query, max_results=5)