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

# TOOL 2: Tìm theo quận (District)
@mcp.tool()
def find_shops_by_location(location_name: str) -> str:
    """Tìm kiếm các tiệm dựa trên từ khóa địa điểm hoặc quận."""
    data = load_db()
    # Logic tìm kiếm đơn giản theo tên hoặc tọa độ (nếu có thêm field district)
    results = [s for s in data if location_name.lower() in s['name'].lower()]
    return json.dumps(results, ensure_ascii=False)

# TOOL 3: Chuẩn bị dữ liệu để LLM "Judge" (Bằng chứng thẩm định)
# server/mcp_server.py
@mcp.tool()
def get_audit_evidence(shop_id: str) -> str:
    """Trả về toàn bộ chỉ số và review khách hàng để AI thực hiện chấm điểm."""
    data = load_db() # Hàm load JSON của ông
    for shop in data:
        if shop['id'] == shop_id:
            return json.dumps({
                "name": shop['name'],
                "raw_metrics": shop['metrics'],
                "reviews": [r['text'] for r in shop['top_reviews']]
            }, ensure_ascii=False)
    return "Not found"

if __name__ == "__main__":
    mcp.run()