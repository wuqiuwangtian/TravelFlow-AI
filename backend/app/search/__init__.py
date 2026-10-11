"""检索接入层 —— 可插拔 Provider（规划 §7）。"""

from app.search.base import HttpSearchProvider, SearchProvider, SearchResult
from app.search.factory import create_search_provider

__all__ = [
    "HttpSearchProvider",
    "SearchProvider",
    "SearchResult",
    "create_search_provider",
]
