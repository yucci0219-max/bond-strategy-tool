#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抢权配债策略计算器 (Bond Snapshot Strategy Calculator)
=====================================================

核心功能:
1. 安全垫计算    - 配债收益能否覆盖正股下跌风险
2. 盈亏平衡点    - 正股最多能跌多少才不亏
3. 抢权时机分析  - 股权登记日前后的持仓建议
4. 策略回测框架  - 基于历史配债数据的胜率统计

作者: WorkBuddy
日期: 2026-06-28
"""

import math
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class BondIssue:
    """可转债发行数据"""
    stock_code: str           # 正股代码，如 "601899"
    stock_name: str           # 正股名称，如 "紫金矿业"
    bond_code: str            # 转债代码（发行后填入）
    bond_name: str            # 转债名称，如 "紫银转债"

    # 关键日期
    announcement_date: str    # 发行公告日 (YYYY-MM-DD)
    record_date: str          # 股权登记日 (YYYY-MM-DD)
    subscription_date: str    # 配售/申购日 (YYYY-MM-DD)
    listing_date: str         # 上市日期 (YYYY-MM-DD, 发行后填入)

    # 发行参数
    total_scale: float        # 发行规模（亿元）
    bond_price: float = 100.0  # 转债面值，通常100元

    # 配售比例（每张转债需持多少股）
    # 例如：每股配售1.5元面值 → 每手(1000元)需持股 = 1000/1.5 = 667股
    allotment_per_share: float = 0.0  # 每股配售金额（元面值）

    # 当前市场数据（需实时更新）
    stock_price: float = 0.0   # 正股当前价（元）
    bond_est_premium: float = 20.0  # 预估上市溢价率（%），保守取15-25%

    # 历史数据（用于回测）
    drop_on_record_day: float = 0.0   # 股权登记日当天正股跌幅（%）
    drop_after_record: float = 0.0    # 股权登记日后正股累计跌幅（%）
    bond_listing_price: float = 0.0    # 转债上市首日价格（元）

    def __post_init__(self):
        if isinstance(self.announcement_date, str) and self.announcement_date:
            self._ann_dt = datetime.strptime(self.announcement_date, "%Y-%m-%d")
        if isinstance(self.record_date, str) and self.record_date:
            self._rec_dt = datetime.strptime(self.record_date, "%Y-%m-%d")
        if isinstance(self.subscription_date, str) and self.subscription_date:
            self._sub_dt = datetime.strptime(self.subscription_date, "%Y-%m-%d")


# ─────────────────────────────────────────────
# 核心计算引擎
# ─────────────────────────────────────────────

class BondStrategyCalculator:
    """
    抢权配债策略计算器

    核心公式:
    - 百元股票配债额 = (每股配债额 / 正股价) × 100
    - 安全垫 = (百元配债额 / 100) × 预估转债溢价率
    - 盈亏平衡点 = 安全垫对应的正股跌幅上限
    - 实际收益 = 转债卖出收益 - 正股持仓亏损
    """

    def __init__(self, data: BondIssue):
        self.d = data

    def shares_per_bond(self) -> float:
        """
        每张转债(1000元面值)需要持有多少股正股
        公式: 1000 / 每股配售金额
        """
        if self.d.allotment_per_share <= 0:
            return 0.0
        return 1000.0 / self.d.allotment_per_share

    def bonds_per_100_shares(self) -> float:
        """
        每100股正股能配到多少张转债(面值1000元/手)
        公式: (100 × 每股配债额) / 1000
        """
        if self.d.allotment_per_share <= 0:
            return 0.0
        return (100.0 * self.d.allotment_per_share) / 1000.0

    def bond_value_per_10k(self) -> float:
        """
        每万元市值正股可获配的转债面值（元）
        公式: (10000 / 正股价) × 每股配债额
        """
        if self.d.stock_price <= 0:
            return 0.0
        shares = 10000.0 / self.d.stock_price
        return shares * self.d.allotment_per_share

    def expected_bond_profit_per_10k(self) -> float:
        """
        每万元市值正股，通过配债预期的收益（元）
        公式: 获配转债面值 × (预估上市价 - 100) / 100
        """
        bond_face_value = self.bond_value_per_10k()
        expected_price = 100.0 + self.d.bond_est_premium
        profit = bond_face_value * (expected_price - 100.0) / 100.0
        return profit

    def safety_cushion(self) -> float:
        """
        安全垫（%）
        定义: 配债预期收益能覆盖正股多少百分比的下跌
        公式: (预期配债收益 / 正股市值) × 100%
        """
        profit = self.expected_bond_profit_per_10k()
        if profit <= 0:
            return 0.0
        return (profit / 10000.0) * 100.0

    def max_acceptable_drop(self) -> float:
        """
        正股最大可承受跌幅（%）
        超过这个跌幅，配债收益无法覆盖正股亏损
        即安全垫的百分比数值
        """
        return self.safety_cushion()

    def optimal_position(self, total_capital: float = 100000.0) -> Dict:
        """
        推荐的最优仓位配置

        参数:
            total_capital: 总资金（元），默认10万

        返回:
            包含建议持仓股数、配债手数、资金分配等
        """
        if self.d.stock_price <= 0 or self.d.allotment_per_share <= 0:
            return {"error": "缺少正股价或配债比例数据"}

        # 每手转债(10张×100元)需要的正股数
        shares_per_hand = math.ceil(1000.0 / self.d.allotment_per_share)

        # 取整百股（A股最小交易单位）
        shares_per_hand = math.ceil(shares_per_hand / 100) * 100

        # 配1手转债需要的资金
        capital_per_hand = shares_per_hand * self.d.stock_price

        # 在总资金约束下，最多能配多少手
        max_hands = int(total_capital / capital_per_hand)

        # 实际投入资金
        actual_capital = max_hands * capital_per_hand
        actual_shares = max_hands * shares_per_hand

        # 获配转债面值
        allotted_face_value = max_hands * 1000.0  # 1手=1000元面值

        # 预估转债收益
        expected_bond_price = 100.0 + self.d.bond_est_premium
        expected_bond_profit = max_hands * (expected_bond_price - 100.0) * 10  # 1手=10张

        # 安全垫
        cushion = self.safety_cushion()

        return {
            "total_capital": total_capital,
            "shares_per_hand": shares_per_hand,
            "max_hands": max_hands,
            "actual_capital": actual_capital,
            "actual_shares": actual_shares,
            "capital_usage_pct": round(actual_capital / total_capital * 100, 1),
            "allotted_face_value": allotted_face_value,
            "expected_bond_profit": round(expected_bond_profit, 2),
            "safety_cushion_pct": round(cushion, 2),
            "breakeven_drop_pct": round(cushion, 2),
            "note": (
                f"配{max_hands}手转债需买{actual_shares}股，"
                f"约投入{actual_capital/10000:.1f}万元；"
                f"安全垫{cushion:.1f}%，正股跌超{cushion:.1f}%才亏钱"
            )
        }

    def scenario_analysis(self) -> List[Dict]:
        """
        情景分析：不同正股跌幅下的综合收益率
        """
        if self.d.stock_price <= 0:
            return [{"error": "缺少正股价数据"}]

        position = self.optimal_position(100000.0)
        if "error" in position:
            return [position]

        hands = position["max_hands"]
        shares = position["actual_shares"]
        capital = position["actual_capital"]

        scenarios = []
        for drop_pct in [-2, -1, 0, 1, 2, 3, 5, 8, 10, 15, 20]:
            # 正股亏损
            stock_loss = capital * (drop_pct / 100.0)

            # 转债收益（假设按预估溢价卖出）
            bond_profit = position["expected_bond_profit"]

            # 综合收益
            total_profit = bond_profit + stock_loss  # stock_loss为负
            total_return_pct = (total_profit / capital) * 100.0

            scenarios.append({
                "stock_drop_pct": drop_pct,
                "stock_loss": round(stock_loss, 2),
                "bond_profit": round(bond_profit, 2),
                "total_profit": round(total_profit, 2),
                "total_return_pct": round(total_return_pct, 2),
                "is_profitable": total_profit > 0,
            })

        return scenarios

    def timing_analysis(self) -> Dict:
        """
        抢权时机分析

        经验规律（基于历史统计）:
        - T-5到T-1: 抢权资金入场，正股往往有正收益
        - T日(股权登记日): 部分资金卖出兑现，正股承压
        - T+1日: 除权除息，无配债权，正股通常继续下跌
        - 转债上市日: 卖出转债兑现收益
        """
        timing = {
            "phases": [
                {
                    "phase": "抢权期 (T-5 至 T-1)",
                    "description": "策略买入窗口，正股可能因抢权资金而上涨",
                    "action": "逐步建仓，避免一天内大量买入推高成本",
                    "risk": "抢权过热导致正股提前上涨，压缩安全垫",
                },
                {
                    "phase": "股权登记日 (T日)",
                    "description": f"收盘必须持仓，晚上清算后获得配债权",
                    "action": "持仓过夜，确保有配债权；次日可卖出正股",
                    "risk": "当日尾盘有跳水风险，部分资金抢跑卖出",
                },
                {
                    "phase": "除权日 (T+1)",
                    "description": "获得配债权，正股可卖出",
                    "action": "卖出正股（如策略是不持股仅抢权），申购/缴款转债",
                    "risk": "正股可能因除权+抛压而低开",
                },
                {
                    "phase": "转债上市日",
                    "description": "转债一般在申购后2-4周上市",
                    "action": "上市首日卖出转债，完成策略闭环",
                    "risk": "如遇市场调整，转债溢价率可能低于预期",
                },
            ]
        }

        # 计算关键日期
        if hasattr(self.d, '_rec_dt'):
            rec_dt = self.d._rec_dt
            timing["key_dates"] = {
                "抢权窗口开始": (rec_dt - timedelta(days=5)).strftime("%Y-%m-%d"),
                "股权登记日": self.d.record_date,
                "除权日(可卖股)": (rec_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                "转债预计上市": (
                    datetime.strptime(self.d.subscription_date, "%Y-%m-%d") + timedelta(days=21)
                ).strftime("%Y-%m-%d") if self.d.subscription_date else "待定",
            }

        return timing

    def full_report(self, total_capital: float = 100000.0) -> str:
        """生成完整文字报告"""
        pos = self.optimal_position(total_capital)
        scenes = self.scenario_analysis()
        timing = self.timing_analysis()

        lines = []
        lines.append("=" * 55)
        lines.append(f"  【{self.d.stock_name}({self.d.stock_code})】抢权配债策略分析报告")
        lines.append("=" * 55)
        lines.append("")

        # 基础信息
        lines.append("【基础数据】")
        lines.append(f"  正股价格:     {self.d.stock_price:.2f} 元")
        lines.append(f"  每股配债额:   {self.d.allotment_per_share:.4f} 元（面值）")
        lines.append(f"  每手需持股:   {pos.get('shares_per_hand', 'N/A')} 股")
        lines.append(f"  预估转债溢价: {self.d.bond_est_premium:.1f}%")
        lines.append(f"  股权登记日:   {self.d.record_date}")
        lines.append("")

        # 仓位建议
        lines.append("【推荐仓位】")
        if "error" not in pos:
            lines.append(f"  总资金:       {total_capital/10000:.1f} 万元")
            lines.append(f"  建议配债:     {pos['max_hands']} 手（{pos['allotted_face_value']/10000:.2f} 万元面值）")
            lines.append(f"  需持股:       {pos['actual_shares']} 股")
            lines.append(f"  投入资金:     {pos['actual_capital']/10000:.2f} 万元（占{post['capital_usage_pct']:.1f}%）")
            lines.append(f"  预估转债收益: {pos['expected_bond_profit']:.0f} 元")
            lines.append(f"  安全垫:       {pos['safety_cushion_pct']:.2f}%")
            lines.append(f"  盈亏平衡点:   正股下跌 {pos['breakeven_drop_pct']:.2f}%")
            lines.append("")

        # 情景分析
        lines.append("【情景分析】（正股跌幅 → 综合收益）")
        lines.append(f"  {'跌幅':>6}  {'正股亏损':>10}  {'转债收益':>10}  {'综合收益':>10}  {'收益率':>8}  {'盈亏':>4}")
        lines.append("  " + "-" * 60)
        for s in scenes:
            flag = "✅盈利" if s["is_profitable"] else "❌亏损"
            lines.append(
                f"  {s['stock_drop_pct']:>5.0f}%  "
                f"{s['stock_loss']:>10.0f}  "
                f"{s['bond_profit']:>10.0f}  "
                f"{s['total_profit']:>10.0f}  "
                f"{s['total_return_pct']:>7.2f}%  "
                f"{flag}"
            )
        lines.append("")

        # 时机分析
        lines.append("【操作时机】")
        for phase in timing.get("phases", []):
            lines.append(f"  ▸ {phase['phase']}")
            lines.append(f"    {phase['description']}")
            lines.append(f"    操作: {phase['action']}")
            lines.append(f"    风险: {phase['risk']}")
            lines.append("")

        if "key_dates" in timing:
            lines.append("  关键日期:")
            for k, v in timing["key_dates"].items():
                lines.append(f"    {k}: {v}")
            lines.append("")

        # 策略评级
        cushion = pos.get('safety_cushion_pct', 0)
        lines.append("【策略评级】")
        if cushion >= 5.0:
            rating = "★★★★★ 高安全垫，积极参与"
        elif cushion >= 3.0:
            rating = "★★★★☆ 中等安全垫，可参与"
        elif cushion >= 1.5:
            rating = "★★★☆☆ 安全垫偏低，谨慎参与"
        else:
            rating = "★★☆☆☆ 安全垫不足，不建议"
        lines.append(f"  {rating}")
        lines.append(f"  （安全垫 {cushion:.2f}% — 正股跌超 {cushion:.2f}% 才亏钱）")
        lines.append("")
        lines.append("  风险提示: 转债上市溢价率存在不确定性，正股下跌可能超预期")
        lines.append("=" * 55)

        return "\n".join(lines)


# ─────────────────────────────────────────────
# 批量分析工具
# ─────────────────────────────────────────────

def batch_analyze(issues: List[BondIssue], capital: float = 100000.0) -> str:
    """批量分析多只配债标的，输出对比表"""
    lines = []
    lines.append("\n【批量配债机会扫描】")
    lines.append(f"  分析资金规模: {capital/10000:.1f} 万元")
    lines.append("")
    lines.append(f"  {'代码':<8} {'名称':<8} {'股价':>7} {'配债额':>8} {'安全垫':>7} {'每手股数':>8} {'评级':>12}")
    lines.append("  " + "-" * 70)

    results = []
    for issue in issues:
        calc = BondStrategyCalculator(issue)
        pos = calc.optimal_position(capital)
        if "error" in pos:
            cushion = 0
            rating = "数据不足"
            hands_shares = "N/A"
        else:
            cushion = pos["safety_cushion_pct"]
            hands_shares = str(pos["shares_per_hand"])
            if cushion >= 5.0:
                rating = "★★★★★"
            elif cushion >= 3.0:
                rating = "★★★★☆"
            elif cushion >= 1.5:
                rating = "★★★☆☆"
            else:
                rating = "★★☆☆☆"

        results.append({
            "issue": issue,
            "cushion": cushion,
            "pos": pos,
            "rating": rating,
            "hands_shares": hands_shares,
        })

    # 按安全垫排序
    results.sort(key=lambda x: x["cushion"], reverse=True)

    for r in results:
        issue = r["issue"]
        pos = r["pos"]
        lines.append(
            f"  {issue.stock_code:<8} {issue.stock_name:<8} "
            f"{issue.stock_price:>7.2f} "
            f"{issue.allotment_per_share:>8.4f} "
            f"{r['cushion']:>6.2f}% "
            f"{r['hands_shares']:>8} "
            f"{r['rating']:>12}"
        )

    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 历史回测模块
# ─────────────────────────────────────────────

@dataclass
class BacktestRecord:
    """单只配债的历史回测记录"""
    stock_code: str
    stock_name: str
    record_date: str       # 股权登记日
    buy_date: str          # 实际买入日
    buy_price: float       # 买入价
    sell_price: float      # 卖出价（正股）
    stock_pnl_pct: float   # 正股盈亏%
    bond_profit_per_hand: float  # 每手转债收益（元）
    hands: int             # 配债手数
    total_return_pct: float  # 综合收益率%
    hold_days: int         # 持仓天数


def backtest_strategy(records: List[BacktestRecord]) -> Dict:
    """
    回测抢权配债策略的历史表现

    统计维度:
    - 胜率（盈利次数/总次数）
    - 平均收益率
    - 最大回撤
    - 正股亏损 vs 转债盈利的贡献占比
    """
    if not records:
        return {"error": "无回测数据"}

    wins = [r for r in records if r.total_return_pct > 0]
    losses = [r for r in records if r.total_return_pct <= 0]

    total_returns = [r.total_return_pct for r in records]
    avg_return = sum(total_returns) / len(total_returns)
    win_rate = len(wins) / len(records) * 100.0

    best = max(records, key=lambda r: r.total_return_pct)
    worst = min(records, key=lambda r: r.total_return_pct)

    # 正股亏损次数
    stock_loss_count = sum(1 for r in records if r.stock_pnl_pct < 0)

    return {
        "total_trades": len(records),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "avg_return_pct": round(avg_return, 2),
        "best_trade": {
            "name": best.stock_name,
            "return_pct": round(best.total_return_pct, 2),
        },
        "worst_trade": {
            "name": worst.stock_name,
            "return_pct": round(worst.total_return_pct, 2),
        },
        "stock_loss_frequency_pct": round(stock_loss_count / len(records) * 100, 1),
        "records": records,
    }


def print_backtest_report(result: Dict) -> str:
    """格式化输出回测报告"""
    if "error" in result:
        return f"回测失败: {result['error']}"

    lines = []
    lines.append("\n" + "=" * 50)
    lines.append("  抢权配债策略 — 历史回测报告")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"  回测样本数:   {result['total_trades']} 次")
    lines.append(f"  胜率:         {result['win_rate_pct']:.1f}%")
    lines.append(f"  平均收益率:   {result['avg_return_pct']:.2f}%")
    lines.append(f"  最佳案例:     {result['best_trade']['name']} ({result['best_trade']['return_pct']:.2f}%)")
    lines.append(f"  最差案例:     {result['worst_trade']['name']} ({result['worst_trade']['return_pct']:.2f}%)")
    lines.append(f"  正股亏损频率: {result['stock_loss_frequency_pct']:.1f}%（正股跌的次数占比）")
    lines.append("")
    lines.append("  明细:")
    lines.append(f"  {'名称':<8} {'买入价':>7} {'卖出价':>7} {'正股盈亏':>8} {'转债收益':>10} {'综合收益':>8} {'天数':>4}")
    lines.append("  " + "-" * 60)
    for r in result["records"]:
        lines.append(
            f"  {r.stock_name:<8} {r.buy_price:>7.2f} {r.sell_price:>7.2f} "
            f"{r.stock_pnl_pct:>7.2f}% {r.bond_profit_per_hand * r.hands:>10.0f} "
            f"{r.total_return_pct:>7.2f}% {r.hold_days:>4}d"
        )
    lines.append("=" * 50)
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI 交互入口
# ─────────────────────────────────────────────

def cli_input_issue() -> BondIssue:
    """交互式输入配债数据"""
    print("\n--- 输入配债数据 ---")
    stock_code = input("正股代码 (如 601899): ").strip()
    stock_name = input("正股名称 (如 紫金矿业): ").strip()

    announcement_date = input("发行公告日 (YYYY-MM-DD, 回车跳过): ").strip()
    record_date = input("股权登记日 (YYYY-MM-DD): ").strip()
    subscription_date = input("申购日 (YYYY-MM-DD, 回车=登记日+1): ").strip()
    if not subscription_date:
        try:
            dt = datetime.strptime(record_date, "%Y-%m-%d") + timedelta(days=1)
            subscription_date = dt.strftime("%Y-%m-%d")
        except Exception:
            subscription_date = record_date

    total_scale = float(input("发行规模（亿元）: ").strip() or "0")
    allotment_per_share = float(input("每股配债额（元面值，如 1.2345）: ").strip() or "0")
    stock_price = float(input("正股当前价（元）: ").strip() or "0")
    bond_est_premium = float(input("预估上市溢价率%（默认20）: ").strip() or "20")

    return BondIssue(
        stock_code=stock_code,
        stock_name=stock_name,
        bond_code="",
        bond_name=f"{stock_name}转债",
        announcement_date=announcement_date,
        record_date=record_date,
        subscription_date=subscription_date,
        listing_date="",
        total_scale=total_scale,
        allotment_per_share=allotment_per_share,
        stock_price=stock_price,
        bond_est_premium=bond_est_premium,
    )


def cli_main():
    """命令行交互主入口"""
    print("=" * 55)
    print("  抢权配债策略计算器 v1.0")
    print("  (输入 Q 随时退出)")
    print("=" * 55)

    while True:
        print("\n请选择操作:")
        print("  1. 分析单只配债")
        print("  2. 批量扫描多只配债")
        print("  3. 查看历史回测示例")
        print("  Q. 退出")
        choice = input("\n选择: ").strip().upper()

        if choice == "Q":
            print("再见！")
            break
        elif choice == "1":
            issue = cli_input_issue()
            calc = BondStrategyCalculator(issue)
            capital = float(input("总资金（元，默认100000）: ").strip() or "100000")
            report = calc.full_report(capital)
            print("\n" + report)
        elif choice == "2":
            print("\n输入多只配债数据（输入空名称结束）:")
            issues = []
            while True:
                name = input("正股名称（空=结束）: ").strip()
                if not name:
                    break
                code = input("正股代码: ").strip()
                price = float(input("正股当前价: ").strip() or "0")
                allot = float(input("每股配债额: ").strip() or "0")
                issues.append(BondIssue(
                    stock_code=code,
                    stock_name=name,
                    bond_code="",
                    bond_name=f"{name}转债",
                    announcement_date="",
                    record_date="",
                    subscription_date="",
                    listing_date="",
                    total_scale=0,
                    allotment_per_share=allot,
                    stock_price=price,
                    bond_est_premium=20.0,
                ))
            if issues:
                capital = float(input("总资金（元，默认100000）: ").strip() or "100000")
                print(batch_analyze(issues, capital))
        elif choice == "3":
            # 内置示例回测数据
            sample_records = [
                BacktestRecord("601899", "紫金矿业", "2024-03-15", "2024-03-12",
                               15.20, 15.05, -0.99, 185.0, 1, 1.21, 3),
                BacktestRecord("600036", "招商银行", "2024-02-20", "2024-02-15",
                               32.50, 31.80, -2.15, 120.0, 1, -0.93, 5),
                BacktestRecord("000858", "五粮液",   "2024-01-18", "2024-01-15",
                               68.30, 69.50, 1.76, 210.0, 1, 2.45, 4),
                BacktestRecord("601012", "隆基绿能", "2023-11-10", "2023-11-07",
                               18.40, 17.20, -6.52, 80.0, 1, -5.72, 4),
                BacktestRecord("300750", "宁德时代", "2024-04-05", "2024-04-01",
                               195.00, 198.50, 1.79, 350.0, 1, 3.58, 5),
            ]
            result = backtest_strategy(sample_records)
            print(print_backtest_report(result))
        else:
            print("无效选择，请重新输入。")


if __name__ == "__main__":
    cli_main()
