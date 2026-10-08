# insect.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List, Tuple, Iterable, Literal
import math
import pandas as pd
import numpy as np

# 可选：从你的 pest config 中导入
# from config import pest_config, pesticide_params

# -----------------------------
# 入参/中间态与输出的结构体
# -----------------------------

HourlyWeatherCols = Literal[
    "DateTime", "temperature_2m", "relative_humidity_2m",
    "wind_speed_10m", "precipitation", "shortwave_radiation"
]

@dataclass
class MigrationResult:
    """单日迁入/越冬到达结果"""
    date: pd.Timestamp
    arrived: bool
    arrival_score: float = 0.0            # 0-1（或任意连续分数）
    reason: Optional[str] = None
    intermediate: Optional[pd.DataFrame] = None

@dataclass
class PhenologyState:
    """龄期分布与关键节点（如 L2 窗口）"""
    date: pd.Timestamp
    stage: str                             # egg/L1/L2/.../L6/pupa/adult
    instar_distribution: Dict[str, float]  # 各龄期占比（和≈1）
    dd_cum: float = 0.0                    # 积温（°C·d）
    window_flags: Dict[str, bool] = field(default_factory=dict)  # e.g. {"L2_window": True}

@dataclass
class PopulationState:
    """虫口密度/数量时间序列的单日状态"""
    date: pd.Timestamp
    densities: Dict[str, float]            # 各龄期个体密度/株（或头/亩）
    total: float
    origin: Optional[str] = None           # e.g. "migration", "reproduction", "carryover"
    intermediate: Optional[Dict[str, Any]] = None

@dataclass
class ControlEvent:
    """施药事件"""
    date: pd.Timestamp
    product_key: str                       # 对应 pesticide_params 的键
    dose_g_ai_ha: float
    notes: Optional[str] = None

@dataclass
class SprayAdvice:
    """喷药时机建议"""
    target_stage: str                      # 典型为 "L2"
    window: Tuple[pd.Timestamp, pd.Timestamp]
    confidence: float                      # 0-1
    rationale: Optional[str] = None

@dataclass
class SpraySimulationResult:
    """喷药后虫口模拟结果"""
    population_series: List[PopulationState]
    events_applied: List[ControlEvent]
    notes: Optional[str] = None

@dataclass
class InsectOutputs:
    """整套输出汇总"""
    migration: List[MigrationResult]
    phenology: List[PhenologyState]
    population: List[PopulationState]
    advice: List[SprayAdvice]
    intermediate: Optional[Dict[str, Any]] = None

# -----------------------------
# 抽象接口
# -----------------------------

class Insect(ABC):
    """
    抽象害虫接口：迁入/越冬、生育期推进、种群动力学、喷药建议与药效耦合。
    子类只需按需覆写若干方法，即可接入统一 API。
    """

    def __init__(self, code: str, config: Dict[str, Any]):
        """
        :param code: 物种代码（如 SPOFRU/OSTFUR/HELARM）
        :param config: pest_config[code]["Global"]（或合并后的区域层 config）
        """
        self.code = code
        self.cfg = config

        # 便捷字段（若缺省则给合理默认）
        self.T_min = float(config.get("T_min", 10.0))
        self.T_opt = float(config.get("T_opt", 25.0))
        self.T_max = float(config.get("T_max", 35.0))

        self.degree_day_per_stage: Dict[str, float] = config.get("degree_day_per_stage", {})
        self.degree_day_per_instar: Dict[str, float] = config.get("degree_day_per_instar", {})
        self.instar_susceptibility: Dict[str, float] = config.get("instar_susceptibility", {})
        self.control_stage_window: Dict[str, Iterable[str]] = config.get("control_stage_window", {})

        self.migration_cfg: Dict[str, Any] = config.get("migration", {"enabled": False})
        self.overwinter: bool = bool(config.get("overwinter", False))

        self.fecundity: float = float(config.get("fecundity", 800))
        self.survival_rate: float = float(config.get("survival_rate", 0.6))

        # 推荐药剂（名称在 pesticide_params 中查找）
        self.recommended_products: List[str] = config.get("recommended_products", [])

    # -------- 顶层编排（可直接调用这一层跑完全部流程） --------

    def run(
        self,
        weather: pd.DataFrame,
        *,
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
        external_alerts: Optional[pd.DataFrame] = None,
        initial_population: Optional[Dict[str, float]] = None,
        control_events: Optional[List[ControlEvent]] = None,
        pesticide_params: Optional[Dict[str, Any]] = None,
    ) -> InsectOutputs:
        """
        完整管线：迁入 → 龄期推进 → 种群动力 → 喷药建议 → 药效耦合（可选）
        """
        dfw = weather.copy()
        if "DateTime" not in dfw.columns:
            raise ValueError("weather DataFrame must include 'DateTime' column.")
        dfw["DateTime"] = pd.to_datetime(dfw["DateTime"])
        if start_date:
            dfw = dfw[dfw["DateTime"] >= pd.to_datetime(start_date)]
        if end_date:
            dfw = dfw[dfw["DateTime"] <= pd.to_datetime(end_date)]
        dfw = dfw.sort_values("DateTime").reset_index(drop=True)

        mig = self.simulate_migration_arrival(dfw, external_alerts=external_alerts)
        phe = self.simulate_phenology(dfw)
        pop = self.simulate_population(dfw, phenology=phe, initial_population=initial_population)

        adv = self.recommend_spray_windows(
            weather=dfw,
            phenology=phe,
            population=pop,
            pesticide_params=pesticide_params,
        )

        # 可选：应用外部 control_events 进行药效模拟
        if control_events and pesticide_params:
            pop = self.apply_pesticide_effects(
                population_series=pop,
                control_events=control_events,
                weather=dfw,
                pesticide_params=pesticide_params
            )

        return InsectOutputs(
            migration=mig,
            phenology=phe,
            population=pop,
            advice=adv
        )

    # -------- 抽象方法（子类通常需要定制/覆写） --------

    @abstractmethod
    def simulate_migration_arrival(
        self,
        weather: pd.DataFrame,
        *,
        external_alerts: Optional[pd.DataFrame] = None
    ) -> List[MigrationResult]:
        """
        迁入/越冬到达模拟：
        - 若 migration.enabled=False，可返回全 False
        - external_alerts：可包含区域虫源、台风路径、雷达/诱捕器等外部信号
        """
        raise NotImplementedError

    @abstractmethod
    def simulate_phenology(self, weather: pd.DataFrame) -> List[PhenologyState]:
        """
        龄期推进（基于积温或发育速率），输出每日龄期分布。
        建议：以 degree_day_per_instar（L1~L6）推进，支持 egg/pupa/adult。
        """
        raise NotImplementedError

    @abstractmethod
    def simulate_population(
        self,
        weather: pd.DataFrame,
        *,
        phenology: List[PhenologyState],
        initial_population: Optional[Dict[str, float]] = None
    ) -> List[PopulationState]:
        """
        种群动力学：
        - 输入龄期分布，结合 fecundity/survival_rate，迭代每日虫口
        - 可并入自然死亡、气象致死、迁入补给等
        """
        raise NotImplementedError

    @abstractmethod
    def recommend_spray_windows(
        self,
        *,
        weather: pd.DataFrame,
        phenology: List[PhenologyState],
        population: List[PopulationState],
        pesticide_params: Optional[Dict[str, Any]] = None
    ) -> List[SprayAdvice]:
        """
        喷药窗口建议：
        - 结合龄期窗口（如 L2 高敏）、虫口阈值、未来 3-5 天喷药气象适宜性
        """
        raise NotImplementedError

    # -------- 可复用的默认实现（子类可直接使用或在内部调用） --------

    # —— favorability 分箱（与 Disease.favorability_to_category 风格一致）——
    def favorability_to_category(self, value: float, categories: Dict[str, str]) -> Optional[str]:
        """
        将连续值映射到区间类别。区间 key 采用 'a-b'；默认 value 以“原始数值”计。
        例如：{"0-6":"UNFAVORABLE","6-12":"FAVORABLE","12-999":"OPTIMAL"}
        """
        for k, v in categories.items():
            lo, hi = [float(x) for x in k.split("-")]
            if lo <= value < hi:
                return v
        return None

    # —— 积温/积温时数计算 —— #
    def hourly_degree(self, t: float, base: float) -> float:
        """小时级有效温度（°C·h），负值按 0 计"""
        return max(0.0, t - base)

    def daily_degree_day(self, tmin: float, tmax: float, base: float) -> float:
        """
        简化日尺度积温（°C·d），子类可改为三角法/Allen 法。
        这里用 (max(0, ((tmin+tmax)/2 - base)))。
        """
        tmean = (tmin + tmax) / 2.0
        return max(0.0, tmean - base)

    def accumulate_degree_hours(
        self, weather: pd.DataFrame, *, base: float
    ) -> pd.DataFrame:
        """
        将小时温度累积为 degree-hours（°C·h），并额外给出 °C·d（/24）
        需要列：DateTime, temperature_2m
        """
        df = weather[["DateTime", "temperature_2m"]].copy()
        df["deg_h"] = df["temperature_2m"].apply(lambda x: self.hourly_degree(x, base=base))
        df["deg_d"] = df["deg_h"] / 24.0
        # 日聚合
        d = (
            df.groupby(df["DateTime"].dt.date)
              .agg({"deg_h": "sum", "deg_d": "sum"})
              .rename_axis("Date").reset_index()
        )
        d["Date"] = pd.to_datetime(d["Date"])
        return d

    # —— 龄期推进（通用）—— #
    def advance_instar_by_degree_day(
        self,
        dd_series: pd.Series,
        per_instar_requirements: Dict[str, float]
    ) -> List[PhenologyState]:
        """
        给定每日积温（°C·d）序列与各龄期所需积温，推导每日龄期与分布。
        简单实现：阈值跨越即进入下一龄期；同日跨越多个阈值时按末态落点。
        需要更精细的“龄期内分布”，可按日内线性分摊或粒子法。
        """
        order = ["egg", "L1", "L2", "L3", "L4", "L5", "L6", "pupa", "adult"]
        need = {k: per_instar_requirements.get(k, 0.0) for k in order}
        cum = 0.0
        current = "egg"
        out: List[PhenologyState] = []

        for date, dd in dd_series.items():
            cum += float(dd)
            # 找到当前龄期
            traversed = 0.0
            for st in order:
                req = need.get(st, 0.0)
                if cum >= traversed + req and req > 0:
                    traversed += req
                    current = st
                else:
                    break

            # 构建一个极简分布（全部质量在当前龄期上）
            instar_dist = {k: 0.0 for k in order}
            instar_dist[current] = 1.0

            window_flags = {"L2_window": (current == "L2")}
            out.append(PhenologyState(
                date=pd.to_datetime(date),
                stage=current,
                instar_distribution=instar_dist,
                dd_cum=cum,
                window_flags=window_flags
            ))
        return out

    # —— Emax 药效模型与修正 —— #
    def emax_efficacy(self, dose: float, Emax: float, ED50: float, hill: float) -> float:
        """Emax/Logistic 剂量-反应：0~1"""
        dose = max(0.0, dose)
        return (Emax * (dose ** hill)) / (ED50 ** hill + dose ** hill + 1e-12)

    def q10_modifier(self, T: float, ref_T: float, Q10: float) -> float:
        """温度修正倍数（>0），如 Q10=1.1 表示+10℃提升10%效力"""
        return Q10 ** ((T - ref_T) / 10.0)

    def residual_modifier(self, days_since: float, half_life_days: float) -> float:
        """残效一阶衰减（0~1）"""
        if half_life_days <= 0:
            return 0.0
        return 0.5 ** (days_since / half_life_days)

    def rainfast_modifier(
        self, hours_since: float, rain_mm: float, rainfast_hours: float, threshold_mm: float, penalty: float
    ) -> float:
        """雨洗修正（<1 时降低药效）"""
        if hours_since < rainfast_hours and rain_mm >= threshold_mm:
            return float(penalty)
        return 1.0

    def instar_modifier(self, instar: str, weights: Dict[str, float]) -> float:
        """龄期敏感性倍乘（缺省=1）"""
        return float(weights.get(instar, 1.0))

    # —— 将药效应用到种群 —— #
    def apply_single_event_effect(
        self,
        population: PopulationState,
        event: ControlEvent,
        *,
        pesticide_params: Dict[str, Any],
        weather_day: Optional[pd.DataFrame],
        instar_weights: Optional[Dict[str, float]] = None,
        T_for_Q10: Optional[float] = None,
        hours_since_spray: Optional[float] = None,
        days_since_spray: Optional[float] = None
    ) -> PopulationState:
        """
        对某一日、某一施药事件，计算各龄期致死并更新虫口。
        - population.densities: {"L1":x, "L2":y, ...}
        - pesticide_params[event.product_key]：读取 Emax/ED50/hill、残效、雨洗、Q10 等
        """
        if event.product_key not in pesticide_params:
            return population

        spec = pesticide_params[event.product_key]
        Emax = float(spec["dose_response"]["Emax"])
        ED50 = float(spec["dose_response"]["ED50"])
        h = float(spec["dose_response"]["hill"])
        base = self.emax_efficacy(event.dose_g_ai_ha, Emax, ED50, h)  # 0~1

        # 温度修正
        temp_mult = 1.0
        tcfg = spec.get("temp_modifier", {"enabled": False})
        if tcfg.get("enabled", False) and T_for_Q10 is not None:
            temp_mult = self.q10_modifier(T_for_Q10, tcfg.get("ref_T", 25.0), tcfg.get("Q10", 1.10))

        # 残效修正
        residual_mult = 1.0
        rcfg = spec.get("residual", {"half_life_days": 0})
        if days_since_spray is not None and rcfg.get("half_life_days", 0) > 0:
            residual_mult = self.residual_modifier(days_since_spray, rcfg["half_life_days"])

        # 雨洗修正（用当天降雨）
        rain_mult = 1.0
        rfc = spec.get("rainfast", None)
        rain_mm = 0.0
        if weather_day is not None and "precipitation" in weather_day.columns:
            rain_mm = float(np.nan_to_num(weather_day["precipitation"].sum()))
        if rfc and hours_since_spray is not None:
            rain_mult = self.rainfast_modifier(
                hours_since_spray, rain_mm, rfc.get("rainfast_hours", 2),
                rfc.get("rainfast_threshold_mm", 5.0), rfc.get("rain_penalty", 0.85)
            )

        # 综合倍乘（龄期因子逐龄期单独乘）
        new_dens = {}
        total = 0.0
        for instar, n in population.densities.items():
            inst_mult = self.instar_modifier(instar, instar_weights or {})
            kill = max(0.0, min(1.0, base * temp_mult * residual_mult * rain_mult * inst_mult))
            surv = max(0.0, 1.0 - kill)
            after = n * surv
            new_dens[instar] = after
            total += after

        return PopulationState(
            date=population.date,
            densities=new_dens,
            total=total,
            origin=population.origin,
            intermediate={"event": event.product_key, "kill_base": base,
                          "temp_mult": temp_mult, "residual_mult": residual_mult,
                          "rain_mult": rain_mult}
        )

    def apply_pesticide_effects(
        self,
        population_series: List[PopulationState],
        control_events: List[ControlEvent],
        *,
        weather: pd.DataFrame,
        pesticide_params: Dict[str, Any]
    ) -> List[PopulationState]:
        """
        对时间序列应用多次施药：
        - 假定事件发生于 event.date 的“当天 0h”
        - 之后各日引入 days_since/hours_since 递增，叠加残效
        - 若同一天多事件，按顺序依次作用（后一剂作用于“上一剂后的剩余虫口”）
        """
        if not control_events:
            return population_series

        dfw = weather.copy()
        dfw["DateTime"] = pd.to_datetime(dfw["DateTime"])
        dfw["Date"] = dfw["DateTime"].dt.normalize()
        day_groups = dict(tuple(dfw.groupby("Date")))

        out: List[PopulationState] = []
        
        for ps in population_series:
            cur = ps
            for ev in control_events:
                if pd.to_datetime(ps.date).normalize() < pd.to_datetime(ev['Date']).normalize():
                    # 还没喷
                    continue
                days_since = (pd.to_datetime(ps.date).normalize() - pd.to_datetime(ev.date).normalize()).days
                hours_since = days_since * 24.0
                wd = day_groups.get(pd.to_datetime(ps.date).normalize(), None)
                T_for_Q10 = None
                if wd is not None and "temperature_2m" in wd.columns and len(wd) > 0:
                    T_for_Q10 = float(np.nanmean(wd["temperature_2m"].values))
                cur = self.apply_single_event_effect(
                    cur, ev,
                    pesticide_params=pesticide_params,
                    weather_day=wd,
                    instar_weights=self.instar_susceptibility,
                    T_for_Q10=T_for_Q10,
                    hours_since_spray=hours_since,
                    days_since_spray=days_since
                )
            out.append(cur)
        return out

    # -------- 便捷检查 --------
    def assert_weather_columns(self, weather: pd.DataFrame) -> None:
        need = {"DateTime", "temperature_2m"}
        missing = need - set(weather.columns)
        if missing:
            raise ValueError(f"weather is missing columns: {missing}")


# ---------------------------------------------------------
# 一个最小可用的“通用夜蛾类”基类实现（可作为示例子类）
# ---------------------------------------------------------

class GenericSpodoptera(Insect):
    """
    基于积温推进 + 简单双 Logistic 增长的最小实现。
    具体物种（SPOFRU/SPODEX）可继承本类仅覆写阈值或迁入逻辑。
    """

    def simulate_migration_arrival(
        self,
        weather: pd.DataFrame,
        *,
        external_alerts: Optional[pd.DataFrame] = None
    ) -> List[MigrationResult]:
        self.assert_weather_columns(weather)
        df = weather.copy()
        df["Date"] = pd.to_datetime(df["DateTime"]).dt.normalize()

        wind_th = float(self.migration_cfg.get("windspeed_threshold", 4.0))
        t_th = float(self.migration_cfg.get("temperature_threshold", 15.0))
        enabled = bool(self.migration_cfg.get("enabled", False))

        daily = (
            df.groupby("Date")
              .agg(wind=("wind_speed_10m", "mean"),
                   tmean=("temperature_2m", "mean"),
                   rain=("precipitation", "sum"))
              .reset_index()
        )

        out: List[MigrationResult] = []
        for _, r in daily.iterrows():
            if not enabled:
                out.append(MigrationResult(date=r["Date"], arrived=False, arrival_score=0.0))
                continue
            score = 0.0
            # 粗略打分：风速越大、温度越高越利于长距离飞行（示例）
            if not np.isnan(r["wind"]):
                score += min(1.0, max(0.0, (r["wind"] - wind_th) / max(1e-9, wind_th)))
            if not np.isnan(r["tmean"]):
                score += min(1.0, max(0.0, (r["tmean"] - t_th) / max(1e-9, t_th)))
            score = max(0.0, min(1.0, score / 2.0))

            arrived = score >= 0.5
            out.append(MigrationResult(
                date=r["Date"], arrived=bool(arrived), arrival_score=float(score)
            ))
        return out

    def simulate_phenology(self, weather: pd.DataFrame) -> List[PhenologyState]:
        self.assert_weather_columns(weather)
        # 用小时温度 → 日积温（基温取 T_min）
        dd = self.accumulate_degree_hours(weather, base=self.T_min)
        dd_series = dd.set_index("Date")["deg_d"]
        # 组装 per-instar 需求（egg/L1~L6/pupa/adult），若未完全给出则补 0
        per_instar = {"egg": self.degree_day_per_stage.get("egg", 0.0)}
        per_instar.update(self.degree_day_per_instar or {})
        per_instar.update({"pupa": self.degree_day_per_stage.get("pupa", 0.0),
                           "adult": self.degree_day_per_stage.get("adult", 0.0)})
        return self.advance_instar_by_degree_day(dd_series, per_instar)

    def simulate_population(
        self,
        weather: pd.DataFrame,
        *,
        phenology: List[PhenologyState],
        initial_population: Optional[Dict[str, float]] = None
    ) -> List[PopulationState]:
        """
        极简示例：以“上一日总量 × 存活 × 简单增长”推进，并按当日龄期分布进行分摊。
        真实模型可换为 Leslie 矩阵或阶段结构模型（含繁殖滞后）。
        """
        if not phenology:
            return []

        # 初始虫口（各龄期）；若未给，设总量 1，并按首日分布分摊
        first_dist = phenology[0].instar_distribution
        if not initial_population:
            total0 = 1.0
            init = {k: total0 * float(v) for k, v in first_dist.items()}
        else:
            init = initial_population

        out: List[PopulationState] = []
        total_prev = sum(init.values())
        cur_dens = init

        # 每日推进（非常简化的增长/存活示例）
        growth_r = 0.05  # 日增长率（示意）
        nat_mort = 0.01  # 自然死亡

        for ps, phe in zip(
            weather["DateTime"].dt.normalize().drop_duplicates().sort_values(),
            phenology
        ):
            # 计算当日总量
            total = max(0.0, total_prev * (1.0 + growth_r) * (1.0 - nat_mort))
            # 按龄期分布
            dist = phe.instar_distribution
            cur_dens = {k: total * float(dist.get(k, 0.0)) for k in dist.keys()}

            out.append(PopulationState(
                date=pd.to_datetime(ps),
                densities=cur_dens,
                total=total,
                origin="carryover",
                intermediate={"growth_r": growth_r, "nat_mort": nat_mort}
            ))
            total_prev = total

        return out

    def recommend_spray_windows(
        self,
        *,
        weather: pd.DataFrame,
        phenology: List[PhenologyState],
        population: List[PopulationState],
        pesticide_params: Optional[Dict[str, Any]] = None
    ) -> List[SprayAdvice]:
        """
        规则示例：
        - 当 L2_window=True 且 当日总虫口 > 分位阈值
        - 同时校验喷药气象：风速 < 3 m/s、未来 6h/24h 无显著降雨
        """
        if not phenology or not population:
            return []

        # 简单阈值：以总虫口的 60 分位作为触发
        totals = np.array([p.total for p in population], dtype=float)
        if len(totals) == 0:
            return []
        trigger = float(np.quantile(totals, 0.6))

        dfw = weather.copy()
        dfw["Date"] = dfw["DateTime"].dt.normalize()
        daily = dfw.groupby("Date").agg(
            wind=("wind_speed_10m", "mean"),
            rain=("precipitation", "sum")
        ).reset_index()

        adv: List[SprayAdvice] = []
        for phe, pop in zip(phenology, population):
            if not phe.window_flags.get("L2_window", False):
                continue
            if pop.total < trigger:
                continue

            row = daily[daily["Date"] == phe.date]
            if len(row) == 0:
                continue
            wind_ok = bool(row["wind"].iloc[0] < 3.0) if not np.isnan(row["wind"].iloc[0]) else True
            rain_ok = bool(row["rain"].iloc[0] < 2.0) if not np.isnan(row["rain"].iloc[0]) else True

            if wind_ok and rain_ok:
                # 给出 2~3 天窗口
                start = phe.date
                end = phe.date + pd.Timedelta(days=2)
                adv.append(SprayAdvice(
                    target_stage="L2",
                    window=(start, end),
                    confidence=0.8,
                    rationale=f"L2窗口且虫口≥{trigger:.2f}，气象适宜"
                ))

        return adv