class SafeWashEvaluator:
    @staticmethod
    def calculate_trust_index(ai_scores):
        """
        Tính điểm tin cậy dựa trên điểm số mà LLM đã thẩm định (Scale 0-5).
        ai_scores: dict {"clean": float, "safe": float, "support": float, "speed": float}
        """
        # Trọng số ưu tiên (Weights)
        # Safety là quan trọng nhất (40%), Clean (30%), Support (20%), Speed (10%)
        
        # Công thức trọng số toán học:
        # $$Score = (Clean \times 0.3) + (Safe \times 0.4) + (Support \times 0.2) + (Speed \times 0.1)$$
        
        s = ai_scores
        weighted_sum = (s['clean'] * 0.3) + (s['safe'] * 0.4) + \
                       (s['support'] * 0.2) + (s['speed'] * 0.1)
        
        # Chuyển đổi từ hệ 5 về hệ 10
        final_score = (weighted_sum / 5.0) * 10.0
        
        # Penalty: Nếu Safe < 3 (AI đánh giá có rủi ro), trừ thẳng 3 điểm overall
        if s['safe'] < 3.0:
            final_score -= 3.0
            
        return round(max(0, min(10, final_score)), 1)