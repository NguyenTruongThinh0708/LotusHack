from mcp.server.fastmcp import FastMCP
import json
from pathlib import Path

mcp = FastMCP("SafeWash-Intelligence-Hub")
BASE_DIR = Path(__file__).parent.parent
DATA_PATH = BASE_DIR / "data" / "stores.json"

def load_db():
    if not DATA_PATH.exists(): return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# TOOL 1: Lấy danh sách tổng quát
@mcp.tool()
def list_all_shops() -> str:
    """Trả về danh sách tất cả các tiệm có trong Database."""
    return json.dumps(load_db(), ensure_ascii=False)

# TOOL 2: Tìm theo quận / địa chỉ / tên
@mcp.tool()
def find_shops_by_location(location_name: str) -> str:
    """Tìm kiếm các tiệm dựa trên từ khóa địa điểm, quận, hoặc tên tiệm."""
    data = load_db()
    query = location_name.lower()
    results = [
        s for s in data
        if query in s.get('name', '').lower()
        or query in s.get('additional_info', {}).get('address', '').lower()
    ]
    return json.dumps(results, ensure_ascii=False)

# TOOL 3: Chi tiết 1 tiệm để audit
@mcp.tool()
def get_audit_evidence(shop_name: str) -> str:
    """Trả về toàn bộ chỉ số và review khách hàng để AI thực hiện chấm điểm. Tìm theo tên tiệm."""
    data = load_db()
    query = shop_name.lower()
    for shop in data:
        if query in shop.get('name', '').lower():
            return json.dumps(shop, ensure_ascii=False)
    return json.dumps({"error": "Shop not found"})

# TOOL 4: So sánh 2+ tiệm
@mcp.tool()
def compare_shops(shop_names: str) -> str:
    """So sánh nhiều tiệm. Truyền tên các tiệm cách nhau bởi dấu '|'. Ví dụ: 'VinaWash|ProWash'."""
    data = load_db()
    queries = [q.strip().lower() for q in shop_names.split("|") if q.strip()]
    results = []
    for shop in data:
        name_lower = shop.get('name', '').lower()
        for q in queries:
            if q in name_lower:
                results.append({
                    "name": shop['name'],
                    "address": shop.get('additional_info', {}).get('address', ''),
                    "metrics": shop.get('metrics', {}),
                    "reviews": [r.get('text', '') for r in shop.get('top_reviews', [])[:2]],
                    "phone": shop.get('phone'),
                    "working_hours": shop.get('working_hours'),
                })
                break
    return json.dumps(results, ensure_ascii=False)

# TOOL 5: Xem busyness / giờ cao điểm
@mcp.tool()
def get_shop_busyness(shop_name: str) -> str:
    """Trả về thông tin giờ cao điểm, live busyness, và giờ hoạt động của tiệm."""
    data = load_db()
    query = shop_name.lower()
    for shop in data:
        if query in shop.get('name', '').lower():
            return json.dumps(shop, ensure_ascii=False)
    return json.dumps({"error": "Shop not found"})

# TOOL 6: Lấy top tiệm an toàn nhất
@mcp.tool()
def get_safest_shops(limit: int = 5) -> str:
    """Trả về các tiệm an toàn nhất dựa trên metric 'safe', sắp xếp giảm dần."""
    data = load_db()
    open_shops = [s for s in data if not s.get('metrics', {}).get('is_closed', False)]
    open_shops.sort(key=lambda s: s.get('metrics', {}).get('safe', 0), reverse=True)
    top = open_shops[:limit]
    results = []
    for shop in top:
        results.append({
            "name": shop['name'],
            "address": shop.get('additional_info', {}).get('address', ''),
            "metrics": shop.get('metrics', {}),
            "phone": shop.get('phone'),
        })
    return json.dumps(results, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()