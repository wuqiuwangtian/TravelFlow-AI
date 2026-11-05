"""校验移动迁移矩阵是否覆盖全部路由声明；不代表分类或移动实现已获验证。"""

import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "post", "put", "patch", "delete"}


def declared_routes():
    routes = set()
    for path in sorted((ROOT / "backend/app/api").glob("routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefix = ""
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "APIRouter"
            ):
                for keyword in node.keywords:
                    if keyword.arg == "prefix":
                        prefix = ast.literal_eval(keyword.value)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in METHODS
                ):
                    route = ast.literal_eval(decorator.args[0])
                    routes.add(
                        (f"{path.stem}.{node.name}", decorator.func.attr.upper(), prefix + route)
                    )
    return routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "docs/internal/MOBILE_CAPABILITIES.md"
    )
    args = parser.parse_args()
    rows = re.findall(
        r"\| `(routes_\w+\.\w+)` \| `(GET|POST|PUT|PATCH|DELETE) ([^`]+)` "
        r"\| (?:LOCAL|ONLINE|MIXED|DEVICE) \|",
        args.manifest.read_text(encoding="utf-8"),
    )
    expected, actual = declared_routes(), set(rows)
    for key in sorted(expected - actual):
        print("Missing classification:", key)
    for key in sorted(actual - expected):
        print("Stale declaration:", key)
    duplicates = len(rows) != len(actual)
    if duplicates:
        print("Duplicate declarations in manifest")
    if expected != actual or duplicates:
        raise SystemExit(1)
    print(f"Covered {len(actual)} route declarations; runtime portability is not implied.")


if __name__ == "__main__":
    main()
