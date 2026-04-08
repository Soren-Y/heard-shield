"""
心盾·心脏风险预警与应急响应系统
功能：
1. 蓝牙接收心率数据
2. 综合风险检测（心率+HRV+变化率）
3. 心脏健康评分（0-100）+ 公式显示
4. 异常指标诊断 + 健康范围提示
5. 高风险弹窗告警 + 自动拨号 + 定位短信
"""

import asyncio
import time
import threading
import subprocess
import requests
import tkinter as tk
from tkinter import ttk, messagebox
from collections import deque
import numpy as np
from bleak import BleakScanner, BleakClient
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
import matplotlib.font_manager as fm

# ====================== 字体设置（兼容中英文） ======================
def setup_chinese_font():
    font_list = [
        'Microsoft YaHei',
        'SimHei',
        'PingFang SC',
        'Heiti SC',
        'WenQuanYi Micro Hei',
        'Noto Sans CJK SC',
        'DejaVu Sans'
    ]
    for font in font_list:
        if any(f.name == font for f in fm.fontManager.ttflist):
            plt.rcParams['font.family'] = font
            print(f"使用字体: {font}")
            return font
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False
    print("使用默认字体")
    return None

setup_chinese_font()

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.animation as animation

# ====================== 心脏风险与健康评分 ======================
class HeartRiskDetector:
    def __init__(self, age=22):
        self.age = age
        self.max_hr = 220 - age
        self.hr_history = deque(maxlen=30)
        self.rr_history = deque(maxlen=20)
        self.last_hr = None
        self.last_ts = None

    def calculate_hr_score(self, hr):
        if 60 <= hr <= 100:
            return 0
        if hr > self.max_hr * 0.85:
            return min(50, 30 + ((hr - self.max_hr * 0.85) / (self.max_hr * 0.15)) * 20)
        elif hr > 100:
            return int((hr - 100) / (self.max_hr * 0.85 - 100) * 30)
        if hr < 40:
            return 40
        elif hr < 50:
            return 20
        elif hr < 60:
            return 10
        return 0

    def calculate_hrv_score(self):
        if len(self.rr_history) < 10:
            return 0
        sdnn = np.std(np.array(self.rr_history))
        if sdnn < 20:
            return 40
        elif sdnn < 30:
            return 30
        elif sdnn < 40:
            return 20
        elif sdnn < 50:
            return 10
        return 0

    def calculate_roc_score(self, hr):
        if self.last_hr is None:
            self.last_hr = hr
            self.last_ts = time.time()
            return 0
        dt = max(0.5, time.time() - self.last_ts)
        change = abs(hr - self.last_hr) / dt
        self.last_hr = hr
        self.last_ts = time.time()
        if change > 10:
            return 10
        elif change > 6:
            return 6
        elif change > 3:
            return 3
        return 0

    def get_heart_health_score(self, hr):
        if 60 <= hr <= 90:
            base = 50
        elif 50 <= hr < 60 or 90 < hr <= 100:
            base = 40
        elif 40 <= hr < 50 or 100 < hr <= 120:
            base = 25
        else:
            base = 0

        if len(self.rr_history) >= 10:
            sdnn = np.std(np.array(self.rr_history))
            if sdnn >= 50:
                hrv_s = 30
            elif sdnn >= 40:
                hrv_s = 25
            elif sdnn >= 30:
                hrv_s = 20
            elif sdnn >= 20:
                hrv_s = 10
            else:
                hrv_s = 0
        else:
            hrv_s = 15

        roc = self.calculate_roc_score(hr)
        stable = 20 - min(20, roc * 2)
        total = base + hrv_s + stable
        health_score = round(max(0, min(100, total)), 1)
        return health_score, base, hrv_s, stable

    def health_level(self, score):
        if score >= 90:
            return "🌟 优秀"
        elif score >= 80:
            return "😊 良好"
        elif score >= 60:
            return "⚡ 一般"
        elif score >= 40:
            return "⚠️ 偏差"
        else:
            return "🔴 危险"

    def diagnose_abnormal(self, hr):
        issues = []
        ranges = []
        if hr < 60:
            issues.append(f"心率过慢（{hr} bpm）")
            ranges.append("健康心率：60~100 bpm")
        elif hr > 100:
            issues.append(f"心率过快（{hr} bpm）")
            ranges.append("健康心率：60~100 bpm")

        if len(self.rr_history) >= 10:
            sdnn = np.std(np.array(self.rr_history))
            if sdnn < 20:
                issues.append(f"HRV偏低（SDNN={sdnn:.1f} ms）")
                ranges.append("健康HRV：>50ms 良好，20~50ms 一般")

        roc = self.calculate_roc_score(hr)
        if roc > 3:
            issues.append(f"心率波动过大（变化率={roc:.1f} bpm/s）")
            ranges.append("健康变化率：<3 bpm/s")

        if not issues:
            return "✅ 所有指标正常", ["各项指标均在健康范围"]
        return " | ".join(issues), ranges

    def add_heart_rate(self, hr, rr=None):
        self.hr_history.append(hr)
        if rr:
            self.rr_history.append(rr)
        else:
            self.rr_history.append(60000 / hr)

        hr_s = self.calculate_hr_score(hr)
        hrv_s = self.calculate_hrv_score()
        roc_s = self.calculate_roc_score(hr)
        total_risk = hr_s + hrv_s + roc_s

        health, base_score, hrv_score, stable_score = self.get_heart_health_score(hr)
        level = self.health_level(health)
        issue, ranges = self.diagnose_abnormal(hr)

        if total_risk >= 60:
            risk_lvl = "🔴 高风险"
            suggest = "立即停止活动，静坐休息，持续不适请紧急就医"
        elif total_risk >= 30:
            risk_lvl = "🟡 中风险"
            suggest = "降低强度，注意休息，密切观察心率变化"
        elif total_risk >= 10:
            risk_lvl = "🟢 低风险"
            suggest = "适当休息，避免剧烈运动"
        else:
            risk_lvl = "✅ 正常"
            suggest = "心率状态良好，继续保持"

        formula = f"健康评分 = 基础心率({base_score}) + HRV({hrv_score}) + 稳定性({stable_score}) = {health}"

        return {
            "hr": hr,
            "risk": total_risk,
            "risk_lvl": risk_lvl,
            "health": health,
            "health_lvl": level,
            "issue": issue,
            "ranges": ranges,
            "suggest": suggest,
            "formula": formula
        }

# ====================== 紧急告警 ======================
class EmergencyAlert:
    def __init__(self, phone="15103750389", name="用户", delay=30):
        self.phone = phone
        self.name = name
        self.delay = delay
        self.cancel = False

    def get_loc(self):
        try:
            j = requests.get("https://ipapi.co/json/", timeout=5).json()
            return f"{j.get('city', '')}{j.get('region', '')}", j.get('lat'), j.get('lon')
        except:
            return "定位失败", None, None

    def call(self):
        try:
            subprocess.Popen(f"tel:{self.phone}")
        except:
            pass

    def sms(self, hr, risk):
        loc, lat, lon = self.get_loc()
        map_link = f"https://map.baidu.com/?x={lon}&y={lat}" if lat and lon else "无定位"
        return f"""【心盾紧急告警】
{self.name} 心率异常！
心率：{hr} bpm  风险分：{risk}
位置：{loc}
地图：{map_link}
请立即确认！"""

    def show(self, hr, risk, gui):
        self.cancel = False
        top = tk.Toplevel()
        top.title("心盾紧急告警")
        top.geometry("550x400")
        top.config(bg="#ff4444")
        top.attributes("-topmost", True)

        tk.Label(top, text="⚠️ 心脏风险告警 ⚠️", font=("微软雅黑", 26, "bold"),
                 bg="#ff4444", fg="white").pack(pady=20)
        tk.Label(top, text=f"心率：{hr} bpm", font=("微软雅黑", 20),
                 bg="#ff4444", fg="white").pack()
        tk.Label(top, text=f"风险评分：{risk}", font=("微软雅黑", 20),
                 bg="#ff4444", fg="white").pack(pady=5)

        tip_var = tk.StringVar(value=f"{self.delay}秒后自动拨号")
        tk.Label(top, textvariable=tip_var, font=("微软雅黑", 14),
                 bg="#ff4444", fg="white").pack(pady=10)

        def do_call():
            self.call()
            self.sms(hr, risk)
            top.destroy()

        def do_cancel():
            self.cancel = True
            top.destroy()

        f = tk.Frame(top, bg="#ff4444")
        f.pack(pady=20)
        tk.Button(f, text="📞 立即拨打", font=("", 14, "bold"), bg="#0a0", fg="white",
                  padx=20, pady=10, command=do_call).pack(side="left", padx=10)
        tk.Button(f, text="✖ 取消", font=("", 14), bg="#666", fg="white",
                  padx=20, pady=10, command=do_cancel).pack(side="left", padx=10)

        def count():
            for i in range(self.delay, -1, -1):
                if self.cancel: return
                tip_var.set(f"⚠️ {i} 秒后自动呼叫紧急联系人")
                time.sleep(1)
            if not self.cancel:
                do_call()

        threading.Thread(target=count, daemon=True).start()

# ====================== GUI界面 ======================
class HeartGuardGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("心盾·心脏健康监护系统")
        self.root.geometry("1000x850")
        self.root.config(bg="#f7f7f7")

        self.hr = tk.IntVar(value=0)
        self.risk = tk.IntVar(value=0)
        self.risk_lbl = tk.StringVar(value="等待连接")
        self.health = tk.DoubleVar(value=0.0)
        self.health_lvl = tk.StringVar(value="-")
        self.issue = tk.StringVar(value="系统启动中")
        self.range_tip = tk.StringVar(value="")
        self.suggest = tk.StringVar(value="")
        self.device = tk.StringVar(value="⚪ 未连接")
        self.formula = tk.StringVar(value="计算公式将在此显示")

        # ====================== 永久保存所有历史数据 ======================
        self.hr_data = []
        self.t_data = []
        self.t0 = time.time()

        self.avg_hr = tk.StringVar(value="--")
        self.max_hr = tk.StringVar(value="--")
        self.min_hr = tk.StringVar(value="--")

        self.ui()

    def ui(self):
        # ====================== 全部缩小 ======================
        title_frame = tk.Frame(self.root, bg="#f7f7f7")
        title_frame.pack(pady=4)
        tk.Label(title_frame, text="❤️ 心盾 · 实时心脏健康监测",
                 font=("微软雅黑", 16, "bold"), bg="#f7f7f7", fg="#d92c2c").pack()

        topf = tk.Frame(self.root, bg="#f7f7f7")
        topf.pack(fill="x", padx=15, pady=1)
        tk.Label(topf, text="设备状态：", font=("微软雅黑", 9), bg="#f7f7f7").pack(side="left")
        tk.Label(topf, textvariable=self.device, font=("微软雅黑", 9, "bold"),
                 fg="#0066cc", bg="#f7f7f7").pack(side="left")
        tk.Label(topf, text=" | 全部历史数据",
                 font=("微软雅黑", 8), bg="#f7f7f7", fg="#888").pack(side="left", padx=5)

        main_frame = tk.Frame(self.root, bg="#f7f7f7")
        main_frame.pack(fill="x", padx=15, pady=3)

        left_col = tk.Frame(main_frame, bg="#f7f7f7")
        left_col.pack(side="left", expand=True, fill="both", padx=2)

        hr_card = tk.Frame(left_col, bg="white", bd=1, relief="ridge")
        hr_card.pack(fill="x", pady=2)
        tk.Label(hr_card, text="💓 实时心率", font=("微软雅黑", 11, "bold"), bg="white", fg="#333").pack(pady=(2, 1))
        tk.Label(hr_card, textvariable=self.hr, font=("Arial", 32, "bold"),
                 fg="#e63946", bg="white").pack()
        tk.Label(hr_card, text="次/分钟", font=("微软雅黑", 8), bg="white", fg="#888").pack(pady=(0, 2))

        health_card = tk.Frame(left_col, bg="white", bd=1, relief="ridge")
        health_card.pack(fill="x", pady=2)
        tk.Label(health_card, text="⭐ 心脏健康评分", font=("微软雅黑", 11, "bold"), bg="white", fg="#333").pack(
            pady=(2, 1))
        tk.Label(health_card, textvariable=self.health, font=("Arial", 28, "bold"),
                 fg="#2e8b57", bg="white").pack()
        tk.Label(health_card, textvariable=self.health_lvl, font=("微软雅黑", 10, "bold"),
                 bg="white", fg="#2e8b57").pack(pady=(0, 1))
        tk.Label(health_card, text="0-100分", font=("微软雅黑", 8), bg="white", fg="#888").pack(pady=(0, 2))

        right_col = tk.Frame(main_frame, bg="#f7f7f7")
        right_col.pack(side="left", expand=True, fill="both", padx=2)

        risk_card = tk.Frame(right_col, bg="white", bd=1, relief="ridge")
        risk_card.pack(fill="both", expand=True, pady=2)
        tk.Label(risk_card, text="⚠️ 风险状态", font=("微软雅黑", 11, "bold"), bg="white", fg="#333").pack(pady=(2, 2))
        tk.Label(risk_card, textvariable=self.risk_lbl, font=("微软雅黑", 13, "bold"), bg="white").pack(pady=2,
                                                                                                        expand=True)
        tk.Label(risk_card, text="综合风险评估", font=("微软雅黑", 8), bg="white", fg="#888").pack(pady=(0, 2))

        # 统计栏缩小
        stats_frame = tk.Frame(self.root, bg="#f7f7f7")
        stats_frame.pack(fill="x", padx=15, pady=2)
        stats_card = tk.Frame(stats_frame, bg="white", bd=1, relief="solid")
        stats_card.pack(fill="x")
        tk.Label(stats_card, text="📊 数据统计", font=("微软雅黑", 9, "bold"), bg="white", fg="#333").pack(side="left",
                                                                                                          padx=10,
                                                                                                          pady=3)
        stats_inner = tk.Frame(stats_card, bg="white")
        stats_inner.pack(side="right", padx=10, pady=3)
        tk.Label(stats_inner, text="平均:", font=("微软雅黑", 8), bg="white").pack(side="left", padx=2)
        tk.Label(stats_inner, textvariable=self.avg_hr, font=("微软雅黑", 8, "bold"), bg="white", fg="#e63946").pack(
            side="left", padx=1)
        tk.Label(stats_inner, text="bpm ", font=("微软雅黑", 7), bg="white").pack(side="left")
        tk.Label(stats_inner, text="最高:", font=("微软雅黑", 8), bg="white").pack(side="left", padx=2)
        tk.Label(stats_inner, textvariable=self.max_hr, font=("微软雅黑", 8, "bold"), bg="white", fg="#ff6600").pack(
            side="left", padx=1)
        tk.Label(stats_inner, text="bpm ", font=("微软雅黑", 7), bg="white").pack(side="left")
        tk.Label(stats_inner, text="最低:", font=("微软雅黑", 8), bg="white").pack(side="left", padx=2)
        tk.Label(stats_inner, textvariable=self.min_hr, font=("微软雅黑", 8, "bold"), bg="white", fg="#3399ff").pack(
            side="left", padx=1)
        tk.Label(stats_inner, text="bpm", font=("微软雅黑", 7), bg="white").pack(side="left")

        # 公式区域缩小
        formula_frame = tk.Frame(self.root, bg="#f0f0f0", bd=1, relief="solid")
        formula_frame.pack(fill="x", padx=15, pady=2)
        tk.Label(formula_frame, text="📐 评分计算公式", font=("微软雅黑", 9, "bold"), bg="#f0f0f0", fg="#0055cc").pack(
            pady=(2, 0))
        tk.Label(formula_frame, textvariable=self.formula, font=("Consolas", 8), bg="#f0f0f0", fg="#333",
                 wraplength=900).pack(pady=(0, 2))

        # 诊断区域缩小
        diag_frame = tk.Frame(self.root, bg="#fff9e6", bd=1, relief="solid")
        diag_frame.pack(fill="x", padx=15, pady=2)
        tk.Label(diag_frame, text="📊 异常指标诊断", font=("微软雅黑", 9, "bold"), bg="#fff9e6", fg="#c92c2c").pack(
            pady=(2, 0))
        tk.Label(diag_frame, textvariable=self.issue, font=("微软雅黑", 8), bg="#fff9e6", fg="#c92c2c",
                 wraplength=900).pack()
        tk.Label(diag_frame, textvariable=self.range_tip, font=("微软雅黑", 7), bg="#fff9e6", fg="#666",
                 wraplength=900).pack(pady=(0, 2))

        # 建议区域缩小
        suggest_frame = tk.Frame(self.root, bg="#e8f4f8", bd=1, relief="solid")
        suggest_frame.pack(fill="x", padx=15, pady=2)
        tk.Label(suggest_frame, text="💡 健康建议", font=("微软雅黑", 9, "bold"), bg="#e8f4f8", fg="#0066cc").pack(
            pady=(2, 0))
        tk.Label(suggest_frame, textvariable=self.suggest, font=("微软雅黑", 8), bg="#e8f4f8", fg="#333",
                 wraplength=900).pack(pady=(0, 2))

        # ====================== 【曲线超大放大】 ======================
        chart_frame = tk.Frame(self.root, bg="white", bd=1, relief="solid")
        chart_frame.pack(fill="both", expand=True, padx=15, pady=8)

        chart_title = tk.Frame(chart_frame, bg="white")
        chart_title.pack(fill="x", pady=3)
        tk.Label(chart_title, text="📈 心率历史趋势", font=("微软雅黑", 12, "bold"), bg="white").pack(side="left",
                                                                                                     padx=5)

        # 曲线最大尺寸
        self.fig, self.ax = plt.subplots(figsize=(9, 4.0), dpi=100, facecolor='white')
        self.ax.set_ylim(30, 180)
        self.ax.set_xlabel("时间 (秒)", fontsize=11)
        self.ax.set_ylabel("心率 (bpm)", fontsize=11)
        self.ax.grid(True, linestyle='--', alpha=0.7)
        self.ax.axhspan(60, 100, alpha=0.2, color='green', label='健康心率范围 60-100 bpm')
        self.ax.yaxis.set_major_locator(MultipleLocator(20))
        self.line, = self.ax.plot([], [], lw=2.5, color="#e63946")
        self.ax.legend(loc='upper right', fontsize=10)
        self.fig.tight_layout(pad=2.0)

        self.canvas = FigureCanvasTkAgg(self.fig, chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)
        self.ani = animation.FuncAnimation(self.fig, self.update_plot, interval=1000, cache_frame_data=False)

        # 状态栏缩小
        status_bar = tk.Frame(self.root, bg="#e0e0e0", height=20)
        status_bar.pack(fill="x", side="bottom")
        tk.Label(status_bar, text="❤️ 心盾健康监护系统", font=("微软雅黑", 8), bg="#e0e0e0", fg="#666").pack(
            side="left", padx=5)

    def update_plot(self, _):
        if len(self.t_data) > 0:
            self.line.set_data(self.t_data, self.hr_data)
            self.ax.set_xlim(0, max(self.t_data) + 10)

            min_y = max(30, min(self.hr_data) - 10)
            max_y = min(180, max(self.hr_data) + 10)
            self.ax.set_ylim(min_y, max_y)
        return self.line,

    def update_stats(self):
        if len(self.hr_data) > 0:
            avg = np.mean(self.hr_data)
            max_val = max(self.hr_data)
            min_val = min(self.hr_data)
            self.avg_hr.set(f"{avg:.1f}")
            self.max_hr.set(f"{max_val}")
            self.min_hr.set(f"{min_val}")

    def update(self, hr, res):
        self.hr.set(hr)
        self.risk_lbl.set(res["risk_lvl"])
        self.health.set(res["health"])
        self.health_lvl.set(res["health_lvl"])
        self.issue.set(res["issue"])
        self.range_tip.set(" ｜ ".join(res["ranges"][:3]))
        self.suggest.set(res["suggest"])
        self.formula.set(res["formula"])

        current_time = time.time() - self.t0
        self.t_data.append(current_time)
        self.hr_data.append(hr)
        self.update_stats()

    def connected(self):
        self.device.set("🟢 已连接心率设备")

# ====================== 蓝牙 ======================
detector = HeartRiskDetector(age=22)
alert = EmergencyAlert(phone="15103750389", name="用户", delay=30)
gui = None
HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"

def handle_heart_rate(sender, data: bytearray):
    try:
        flags = data[0]
        hr = int.from_bytes(data[1:3], "little") if (flags & 1) else data[1]
        res = detector.add_heart_rate(hr)
        gui.update(hr, res)
        if res["risk"] >= 60:
            alert.show(hr, res["risk"], gui)
    except Exception as e:
        print("解析err:", e)

async def ble_task():
    try:
        devs = await BleakScanner.discover(timeout=10)
        target = None
        for d in devs:
            if d.name and any(k in d.name.lower() for k in ["watch", "band", "fit", "honor"]):
                target = d
                break
        if not target:
            messagebox.showwarning("提示", "未找到心率设备")
            return
        gui.connected()
        async with BleakClient(target.address) as client:
            await client.start_notify(HR_CHAR, handle_heart_rate)
            while True:
                await asyncio.sleep(1)
    except Exception as e:
        messagebox.showerror("错误", f"蓝牙失败：{e}")

def start_ble():
    asyncio.run(ble_task())

# ====================== 启动 ======================
if __name__ == "__main__":
    root = tk.Tk()
    gui = HeartGuardGUI(root)
    threading.Thread(target=start_ble, daemon=True).start()
    root.mainloop()