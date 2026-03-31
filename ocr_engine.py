# ocr_engine.py
from paddlex import create_pipeline

class OCREngine:
    def __init__(self):
        self.pipeline = create_pipeline(pipeline="OCR")

    def predict_image(self, img_path):
        try:
            output = self.pipeline.predict(input=img_path, use_doc_orientation_classify=True)
            for res in output:
                # 获取文字、角度以及对应的置信度分数
                raw_texts = res.get("rec_texts", [])
                raw_scores = res.get("rec_scores", []) # 关键：获取每个词的分数
                
                # 过滤：只有置信度 > 0. 且长度 > 1 的才保留
                valid_texts = []
                for text, score in zip(raw_texts, raw_scores):
                    if score > 0.5 and len(text.strip()) > 2:
                        valid_texts.append(text.strip())

                return {
                    "angle": int(res.get("doc_preprocessor_res", {}).get("angle", 0)),
                    "texts": valid_texts,
                    "status": "success"
                }
        except Exception as e:
            return {"angle": -1, "texts": [], "status": f"error: {e}"}
        return {"angle": -1, "texts": [], "status": "empty"}