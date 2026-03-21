class SafeWashEvaluator:
    """
    Đánh giá tiệm rửa xe dựa trên metrics scale -2 đến +2:
        0  = Không đề cập
        1  = Đề cập nhẹ tích cực   |  -1 = Đề cập nhẹ tiêu cực
        2  = Rõ ràng tích cực      |  -2 = Rõ ràng tiêu cực
    Bonus +1 cho 'support' nếu chủ tiệm phản hồi hỗ trợ / xin lỗi.
    """

    # Trọng số: safe quan trọng nhất, rồi đến clean, support, speed, price
    WEIGHTS = {
        "safe":    0.30,
        "clean":   0.25,
        "support": 0.20,
        "speed":   0.15,
        "price":   0.10,
    }

    @staticmethod
    def calculate_trust_index(metrics: dict) -> float:
        r"""
        Tính WashGo Trust Index (0-10) từ metrics scale -2..+2.

        Công thức:
        1. Chuẩn hoá mỗi metric từ [-2, +2] về [0, 1]:  norm = (value + 2) / 4
        2. Weighted sum:  $$ S = \sum_{k} w_k \cdot \text{norm}(k) $$
        3. Scale lên hệ 10:  final = S × 10
        4. Penalty: nếu safe < 0 → trừ 2 điểm
        5. Nếu is_closed → trả 0 luôn
        """
        if metrics.get("is_closed", False):
            return 0.0

        weighted_sum = 0.0
        for key, weight in SafeWashEvaluator.WEIGHTS.items():
            raw = metrics.get(key, 0)
            norm = (raw + 2) / 4.0       # -2→0, 0→0.5, +2→1
            weighted_sum += weight * norm

        final_score = weighted_sum * 10.0

        # Penalty nếu safe tiêu cực
        if metrics.get("safe", 0) < 0:
            final_score -= 2.0

        return round(max(0.0, min(10.0, final_score)), 1)

    @staticmethod
    def risk_label(metrics: dict) -> str:
        """Trả về label rủi ro dựa trên metrics."""
        if metrics.get("is_closed", False):
            return "CLOSED"
        safe = metrics.get("safe", 0)
        if safe <= -2:
            return "HIGH RISK"
        if safe < 0:
            return "CAUTION"
        return "OK"
