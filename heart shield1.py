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

plt.rcParams["font.family"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

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

    # 1. 心率风险分
    def calculate_hr_score(self, hr):
        if 60 <= hr <= 100:
            return 0
        if hr > self.max_hr * 0.85:
            return min(50, 30 + ((hr - self.max_hr*0.85)/(self.max_hr*0.15))*20)
        elif hr > 100:
            return int((hr-100)/(self.max_hr*0.85-100)*30)
        if hr < 40:
            return 40
        elif hr < 50:
            return 20
        elif hr < 60:
            return 10
        return 0

    # 2. HRV风险分
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

    # 3. 心率变化率风险分
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

    # ====================== 【心脏健康评分公式】 ======================
    def get_heart_health_score(self, hr):
        # 基础心率分 0~50
        if 60 <= hr <= 90:
            base = 50
        elif 50 <= hr < 60 or 90 < hr <= 100:
            base = 40
        elif 40 <= hr < 50 or 100 < hr <= 120:
            base = 25
        else:
            base = 0

        # HRV分 0~30
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

        # 稳定性分 0~20
        roc = self.calculate_roc_score(hr)
        stable = 20 - min(20, roc * 2)

        total = base + hrv_s + stable
        health_score = round(max(0, min(100, total)), 1)

        # 返回 总分 + 各项明细（用于界面显示公式）
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

    # ====================== 异常指标诊断 ======================
    def diagnose_abnormal(self, hr):
        issues = []
        ranges = []

        # 心率
        if hr < 60:
            issues.append(f"心率过慢（{hr}）")
            ranges.append("健康心率：60~100 bpm")
        elif hr > 100:
            issues.append(f"心率过快（{hr}）")
            ranges.append("健康心率：60~100 bpm")

        # HRV
        if len(self.rr_history) >= 10:
            sdnn = np.std(np.array(self.rr_history))
            if sdnn < 20:
                issues.append(f"HRV偏低（SDNN={sdnn:.1f}）")
                ranges.append("健康HRV：>50ms 良好，20~50ms 一般")

        # 波动
        roc = self.calculate_roc_score(hr)
        if roc > 3:
            issues.append(f"心率波动过大（变化率={roc:.1f}）")
            ranges.append("健康变化率：<3 bpm/s")

        if not issues:
            return "✅ 所有指标正常", ["各项指标均在健康范围"]
        return " | ".join(issues), ranges

    # ====================== 统一输出 ======================
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

        # 风险等级
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

        # 公式文本
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
            "formula": formula  # 计算公式
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
            return f"{j.get('city','')}{j.get('region','')}", j.get('lat'), j.get('lon')
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

        tk.Label(top, text="⚠️ 心脏风险告警 ⚠️", font=("微软雅黑",26,"bold"),
                 bg="#ff4444",fg="white").pack(pady=20)
        tk.Label(top,text=f"心率：{hr} bpm",font=("微软雅黑",20),
                 bg="#ff4444",fg="white").pack()
        tk.Label(top,text=f"风险评分：{risk}",font=("微软雅黑",20),
                 bg="#ff4444",fg="white").pack(pady=5)

        tip_var = tk.StringVar(value=f"{self.delay}秒后自动拨号")
        tk.Label(top,textvariable=tip_var,font=("微软雅黑",14),
                 bg="#ff4444",fg="white").pack(pady=10)

        def do_call():
            self.call()
            self.sms(hr,risk)
            top.destroy()
        def do_cancel():
            self.cancel = True
            top.destroy()

        f = tk.Frame(top,bg="#ff4444")
        f.pack(pady=20)
        tk.Button(f,text="📞 立即拨打",font=("",14,"bold"),bg="#0a0",fg="white",
                  padx=20,pady=10,command=do_call).pack(side="left",padx=10)
        tk.Button(f,text="✖ 取消",font=("",14),bg="#666",fg="white",
                  padx=20,pady=10,command=do_cancel).pack(side="left",padx=10)

        def count():
            for i in range(self.delay,-1,-1):
                if self.cancel: return
                tip_var.set(f"⚠️ {i} 秒后自动呼叫紧急联系人")
                time.sleep(1)
            if not self.cancel: do_call()

        threading.Thread(target=count,daemon=True).start()
        top.mainloop()

# ====================== GUI界面 ======================
class HeartGuardGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("心盾·心脏健康监护系统")
        self.root.geometry("950x800")
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

        self.hr_data = deque(maxlen=60)
        self.t_data = deque(maxlen=60)
        self.t0 = time.time()

        self.ui()

    def ui(self):
        tk.Label(self.root,text="❤️ 心盾 · 实时心脏健康监测",
                 font=("微软雅黑",26,"bold"),bg="#f7f7f7",fg="#d92c2c").pack(pady=12)

        topf = tk.Frame(self.root,bg="#f7f7f7")
        topf.pack(fill="x",padx=30)
        tk.Label(topf,text="设备：",font=("",13),bg="#f7f7f7").grid(row=0,column=0)
        tk.Label(topf,textvariable=self.device,font=("",13,"bold"),
                 fg="#0066cc",bg="#f7f7f7").grid(row=0,column=1)

        # 卡片1：心率
        c1 = tk.Frame(self.root,bg="white",bd=2,relief="ridge")
        c1.pack(fill="x",padx=30,pady=8)
        tk.Label(c1,text="实时心率",font=("",17),bg="white").pack(pady=8)
        tk.Label(c1,textvariable=self.hr,font=("Arial",52,"bold"),
                 fg="#e63946",bg="white").pack()
        tk.Label(c1,text="BPM",font=("",14),bg="white").pack(pady=2)

        # 卡片2：健康评分
        c2 = tk.Frame(self.root,bg="white",bd=2,relief="ridge")
        c2.pack(fill="x",padx=30,pady=8)
        tk.Label(c2,text="心脏健康评分",font=("",17),bg="white").pack(pady=8)
        tk.Label(c2,textvariable=self.health,font=("Arial",40,"bold"),
                 fg="#2e8b57",bg="white").pack()
        tk.Label(c2,textvariable=self.health_lvl,font=("",16,"bold"),
                 bg="white").pack(pady=2)

        # 公式显示
        tk.Label(self.root,text="📐 评分计算公式",font=("",14,"bold"),
                 bg="#f7f7f7").pack(pady=(8,0))
        tk.Label(self.root,textvariable=self.formula,font=("",11,"bold"),
                 bg="#f7f7f7",fg="#0055cc").pack(pady=2)

        # 卡片3：风险
        c3 = tk.Frame(self.root,bg="white",bd=2,relief="ridge")
        c3.pack(fill="x",padx=30,pady=8)
        tk.Label(c3,text="风险状态",font=("",17),bg="white").pack(pady=6)
        tk.Label(c3,textvariable=self.risk_lbl,font=("",20,"bold"),
                 bg="white").pack(pady=4)

        # 异常指标
        tk.Label(self.root,text="📊 异常指标诊断",font=("",15,"bold"),
                 bg="#f7f7f7").pack(pady=(10,0))
        tk.Label(self.root,textvariable=self.issue,font=("",13),
                 bg="#f7f7f7",fg="#c92c2c").pack()
        tk.Label(self.root,textvariable=self.range_tip,font=("",11),
                 bg="#f7f7f7",fg="#444").pack()

        # 建议
        tk.Label(self.root,text="💡 健康建议",font=("",15,"bold"),
                 bg="#f7f7f7").pack(pady=(10,0))
        tk.Label(self.root,textvariable=self.suggest,font=("",12),
                 bg="#f7f7f7",wraplength=850).pack(pady=(0,10))

        # 曲线
        self.fig, self.ax = plt.subplots(figsize=(10,3),dpi=95)
        self.ax.set_ylim(30,180)
        self.ax.set_title("心率趋势")
        self.ax.grid(True)
        self.line, = self.ax.plot([],[],lw=2,color="#e63946")
        self.canvas = FigureCanvasTkAgg(self.fig,self.root)
        self.canvas.get_tk_widget().pack(padx=30,pady=10,fill="both",expand=True)
        self.ani = animation.FuncAnimation(self.fig,self.update_plot,interval=600)

    def update_plot(self,_):
        if self.t_data:
            self.line.set_data(self.t_data, self.hr_data)
            self.ax.set_xlim(max(0,self.t_data[-1]-60), self.t_data[-1]+5)
        return self.line,

    def update(self,hr,res):
        self.hr.set(hr)
        self.risk_lbl.set(res["risk_lvl"])
        self.health.set(res["health"])
        self.health_lvl.set(res["health_lvl"])
        self.issue.set(res["issue"])
        self.range_tip.set(" ｜ ".join(res["ranges"][:3]))
        self.suggest.set(res["suggest"])
        self.formula.set(res["formula"])  # 显示公式
        self.t_data.append(time.time()-self.t0)
        self.hr_data.append(hr)

    def connected(self):
        self.device.set("🟢 已连接心率设备")

# ====================== 蓝牙 ======================
detector = HeartRiskDetector(age=22)
alert = EmergencyAlert(phone="15103750389", name="用户", delay=30)
gui = None
HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"

# 修复回调参数
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
            if d.name and any(k in d.name.lower() for k in ["watch","band","fit","honor"]):
                target = d
                break
        if not target:
            messagebox.showwarning("提示","未找到心率设备")
            return
        gui.connected()
        async with BleakClient(target.address) as client:
            await client.start_notify(HR_CHAR, handle_heart_rate)
            while True:
                await asyncio.sleep(1)
    except Exception as e:
        messagebox.showerror("错误",f"蓝牙失败：{e}")

def start_ble():
    asyncio.run(ble_task())

# ====================== 启动 ======================
if __name__ == "__main__":
    root = tk.Tk()
    gui = HeartGuardGUI(root)
    threading.Thread(target=start_ble, daemon=True).start()
    root.mainloop()