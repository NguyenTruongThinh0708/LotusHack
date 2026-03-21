class SafeWashEvaluator:
    @staticmethod
    def audit(shop_data):
        m = shop_data['metrics']
        # Trọng số ưu tiên Safety và Support
        score = (m['clean'] * 1.2) + (m['support'] * 1.5) + (m['safe'] * 4.0) + (m['speed'] * 1.0)
        
        # Penalty cho tiệm bị dính flag safe = -1
        is_risky = m['safe'] < 0
        final_score = max(0, min(10, score)) if not is_risky else 2.0
        
        return {
            "final_score": round(final_score, 1),
            "verdict": "SAFE ✅" if not is_risky else "UNSAFE 🛑",
            "is_franchise": m['is_franchise']
        }