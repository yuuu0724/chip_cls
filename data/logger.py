"""数据日志记录模块"""
import csv
import os
from datetime import datetime


class DataLogger:
    def __init__(self, base_dir="results"):
        self.base_dir = base_dir
        self.current_file = None
        self.current_tray = None
        self.batch_count = 0
        # 创建结果目录
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
    
    def start_new_batch(self, tray_id="A0001"):
        """
        开始新一轮检测，创建新的结果文件
        返回当前文件路径
        """
        self.current_tray = tray_id
        self.batch_count += 1
        
        # 生成文件名：tray_id_批次_时间戳
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{tray_id}_batch{self.batch_count}_{timestamp}.csv"
        self.current_file = os.path.join(self.base_dir, filename)
        
        # 初始化表头
        try:
            with open(self.current_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["时间", "料位", "所有识别文本", "识别角度", "检测结果"])
        except Exception as e:
            print(f"创建结果文件失败: {e}")
        
        return self.current_file

    def log_result(self, slot_id, all_text, angle, status):
        """记录单次识别的详细数据"""
        if not self.current_file:
            print("警告：未初始化结果文件，跳过记录")
            return
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_msg = str(all_text).replace("\n", " ").replace("\r", "")
        try:
            with open(self.current_file, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([now, slot_id, clean_msg, angle, status])
        except Exception as e:
            print(f"写入CSV失败: {e}")
