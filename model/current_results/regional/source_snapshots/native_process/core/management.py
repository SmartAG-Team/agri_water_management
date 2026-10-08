
from datetime import date
import datetime
from typing import Any, List,Dict
import pandas as pd
from copy import deepcopy
import numpy as np
from typing import List
from core.crop import Crop
from core.utils import get_disease_insect_weed_status_and_code,get_nutrition_status_and_code
class SprayRecommendation():
    TIMING_WINDOW_CODES = {"MISSED", "CURRENT", "FUTURE", "NOT_PRESENT"}
    NO_ACTION_CODE = "NOT_NEEDED"

    def __init__(self,name:str=None,method:str=None,applied_date:datetime.datetime=None,curative_efficacy:float=0.5,curative_protection_days:float=0,preventive_efficacy:float=0.95,preventive_protection_days:float=0,eradicative_efficacy:bool=False,eradicative_protection_days:float=10)->None:
        '''
        method can be: "SEED","SEED_BOX", "SPRAYING"
        eradicative_protection_days means the effects of fungicide eradicant efficacy on infection. It impacts the former infectous tissues for the whle period of infectous. However the effects decacy with time. This is not handled here.
        '''
        self.name=name
        self.method=method
        self.applied_date=applied_date
        self.curative_efficacy=curative_efficacy
        self.curative_protection_days=curative_protection_days
        self.preventive_efficacy=preventive_efficacy
        self.preventive_protection_days=preventive_protection_days
        self.eradicative_efficacy=eradicative_efficacy
        self.eradicative_protection_days=eradicative_protection_days
    @staticmethod
    def get_missing_end_date_for_a_spray_window(spray_window_end:datetime.date=None,dfspray:pd.DataFrame=None,stress_risks:pd.DataFrame=None):
            '''
            detect the ending
            dfspray is an action dataframe that need derive from field status
            '''
            long_range_stress_risk_after_spray_end = deepcopy(
                stress_risks.loc[stress_risks.Date> spray_window_end]
            )

            status_num = {
                "LOW": 1,
                "MEDIUM": 2,
                "HIGH": 3,
                "PROTECTED": 4,
            }
            for ds in long_range_stress_risk_after_spray_end.eppo_code.unique():
                first_day = long_range_stress_risk_after_spray_end[
                    long_range_stress_risk_after_spray_end.eppo_code == ds
                ].Date.min()
                for ind, row in long_range_stress_risk_after_spray_end[
                    (long_range_stress_risk_after_spray_end.Date > first_day)
                    & (long_range_stress_risk_after_spray_end.eppo_code == ds)
                ].iterrows():
                    today = row.Date
                    yesterday = row.Date + datetime.timedelta(days=-1)
                    today_status = row.stress_risk
                    yesterday_status = long_range_stress_risk_after_spray_end.loc[
                        (
                            long_range_stress_risk_after_spray_end.Date
                            == yesterday
                        )
                        & (long_range_stress_risk_after_spray_end.eppo_code == ds),
                        "stress_risk",
                    ].values[0]
                    if (
                        (status_num[today_status] < status_num[yesterday_status])
                        and (yesterday_status != "PROTECTED")
                        and (today_status != "NOT_SEASONAL")
                    ):
                        long_range_stress_risk_after_spray_end.loc[
                            (
                                long_range_stress_risk_after_spray_end.Date
                                == today
                            )
                            & (long_range_stress_risk_after_spray_end.eppo_code == ds),
                            "stress_risk",
                        ] = yesterday_status
                
            long_range_stress_risk_after_spray_end["stress_risk_number"] = long_range_stress_risk_after_spray_end.stress_risk.apply(
                lambda x: status_num[x]
            )
            field_risks_after_missing = pd.DataFrame(
                columns=["Date", "field_risk"]
            )
            
            for rd in long_range_stress_risk_after_spray_end.Date.unique():
                stress_riskValues = long_range_stress_risk_after_spray_end.loc[
                    long_range_stress_risk_after_spray_end.Date == rd,
                    "stress_risk_number",
                ].values
                stress_risk = Crop.get_field_risk_for_a_day(stress_riskValues,status_num=status_num)
                field_risks_after_missing.loc[len(field_risks_after_missing)] = [
                    rd,
                    stress_risk,
                ]
            trigger_mask = field_risks_after_missing.field_risk == "HIGH"
            if len(field_risks_after_missing.loc[trigger_mask]) > 0:
                return (
                    field_risks_after_missing.loc[trigger_mask, "Date"].min(),
                    True,
                    field_risks_after_missing,
                    field_risks_after_missing.loc[trigger_mask, "Date"].min(),
                )
            elif len(field_risks_after_missing.loc[field_risks_after_missing.field_risk == "PROTECTED"])> 0:
                return (
                    field_risks_after_missing.loc[
                        field_risks_after_missing.field_risk == "PROTECTED", 'Date'].min() + datetime.timedelta(
                        days=-1),
                    False,
                    field_risks_after_missing,
                    None,
                )
            elif (len(field_risks_after_missing.loc[field_risks_after_missing.field_risk != "NOT_SEASONAL"])== 0):
                return (
                    field_risks_after_missing.Date.max(),
                    False,
                    field_risks_after_missing,
                    None,
                    )
            else:
                return (
                    field_risks_after_missing.loc[
                        field_risks_after_missing.field_risk != "NOT_SEASONAL",
                        "Date",
                    ].max(),
                    False,
                    field_risks_after_missing,
                    None,
                )
    @staticmethod
    def _limit_recommendation_windows(
        action_df: pd.DataFrame,
        minimum_recommendation_interval_days: int | None = None,
        max_recommendations_per_season: int | None = None,
    ) -> pd.DataFrame:
        if not minimum_recommendation_interval_days and not max_recommendations_per_season:
            return action_df
        if action_df.empty:
            return action_df

        limited = deepcopy(action_df)
        window = limited.get("treatmentWindowCode", pd.Series(index=limited.index, dtype=object)).fillna("").astype(str).str.upper()
        active_mask = (limited["recommendationCode"] == "RECOMMENDED") & window.isin(["CURRENT", "FUTURE"])
        if not active_mask.any():
            return limited
        if max_recommendations_per_season is not None and max_recommendations_per_season <= 0:
            limited.loc[active_mask, ["recommendationCode", "actionTypeCode"]] = "NOT_NEEDED"
            limited.loc[active_mask, "treatmentWindowCode"] = "NOT_PRESENT"
            limited.loc[active_mask, ["treatmentStartDate", "treatmentEndDate"]] = None
            return limited

        start_values = limited["treatmentStartDate"].where(
            limited["treatmentStartDate"].notna(),
            limited["Date"],
        )
        start_values = pd.to_datetime(start_values).dt.date
        candidate_starts = sorted(set(start_values.loc[active_mask].dropna()))

        kept_starts: list[datetime.date] = []
        for start in candidate_starts:
            if (
                minimum_recommendation_interval_days
                and kept_starts
                and (start - kept_starts[-1]).days < minimum_recommendation_interval_days
            ):
                continue
            kept_starts.append(start)
            if max_recommendations_per_season and len(kept_starts) >= max_recommendations_per_season:
                break

        if not kept_starts:
            limited.loc[active_mask, ["recommendationCode", "actionTypeCode"]] = "NOT_NEEDED"
            limited.loc[active_mask, "treatmentWindowCode"] = "NOT_PRESENT"
            limited.loc[active_mask, ["treatmentStartDate", "treatmentEndDate"]] = None
            return limited

        keep_mask = active_mask & start_values.isin(kept_starts)
        drop_mask = active_mask & ~keep_mask
        limited.loc[drop_mask, ["recommendationCode", "actionTypeCode"]] = "NOT_NEEDED"
        limited.loc[drop_mask, "treatmentWindowCode"] = "NOT_PRESENT"
        limited.loc[drop_mask, ["treatmentStartDate", "treatmentEndDate"]] = None
        return limited

    @staticmethod
    def _suppress_recommendations_before(action_df: pd.DataFrame, earliest_start: datetime.date | None) -> pd.DataFrame:
        if earliest_start is None or action_df.empty:
            return action_df
        limited = deepcopy(action_df)
        active_mask = limited["recommendationCode"] == "RECOMMENDED"
        start_values = limited["treatmentStartDate"].where(
            limited["treatmentStartDate"].notna(),
            limited["Date"],
        )
        start_values = pd.to_datetime(start_values).dt.date
        drop_mask = active_mask & (start_values < earliest_start)
        limited.loc[drop_mask, ["recommendationCode", "actionTypeCode"]] = "NOT_NEEDED"
        limited.loc[drop_mask, "treatmentWindowCode"] = "NOT_PRESENT"
        limited.loc[drop_mask, ["treatmentStartDate", "treatmentEndDate"]] = None
        return limited

    @staticmethod
    def normalize_plant_protection_actions(action_df: pd.DataFrame) -> pd.DataFrame:
        if action_df.empty:
            return action_df
        limited = deepcopy(action_df)
        window = limited["treatmentWindowCode"].fillna("NOT_PRESENT").astype(str).str.upper()
        limited["treatmentWindowCode"] = window

        no_window_mask = window == "NOT_PRESENT"
        missed_mask = window == "MISSED"
        active_mask = window.isin(["FUTURE", "CURRENT"])
        necessary_mask = limited["recommendationCode"].astype(str).str.upper() == "NECESSARY"

        limited.loc[no_window_mask, ["recommendationCode", "actionTypeCode"]] = "NOT_NEEDED"
        limited.loc[no_window_mask, ["treatmentStartDate", "treatmentEndDate"]] = None

        limited.loc[missed_mask, "recommendationCode"] = "RECOMMENDED"
        limited.loc[missed_mask, "actionTypeCode"] = "TREAT"
        limited.loc[active_mask, "recommendationCode"] = "RECOMMENDED"
        limited.loc[active_mask, "actionTypeCode"] = "TREAT"

        limited.loc[necessary_mask, "recommendationCode"] = "RECOMMENDED"
        limited.loc[necessary_mask, "actionTypeCode"] = "TREAT"
        limited.loc[necessary_mask, "treatmentWindowCode"] = "MISSED"
        return limited

    @staticmethod
    def apply_decision_date(action_df: pd.DataFrame, decision_date: datetime.date | str | None) -> pd.DataFrame:
        limited = SprayRecommendation.normalize_plant_protection_actions(action_df)
        if decision_date is None or limited.empty:
            return limited

        decision_day = pd.to_datetime(decision_date).normalize()
        start_values = pd.to_datetime(limited["treatmentStartDate"], errors="coerce").dt.normalize()
        end_values = pd.to_datetime(limited["treatmentEndDate"], errors="coerce").dt.normalize()
        has_window = start_values.notna() & end_values.notna()

        missed_mask = has_window & (end_values < decision_day)
        current_mask = has_window & (start_values <= decision_day) & (end_values >= decision_day)
        future_mask = has_window & (start_values > decision_day)

        limited.loc[missed_mask, "treatmentWindowCode"] = "MISSED"
        limited.loc[missed_mask, "recommendationCode"] = "RECOMMENDED"
        limited.loc[missed_mask, "actionTypeCode"] = "TREAT"

        limited.loc[current_mask, "treatmentWindowCode"] = "CURRENT"
        limited.loc[current_mask, "recommendationCode"] = "RECOMMENDED"
        limited.loc[current_mask, "actionTypeCode"] = "TREAT"

        limited.loc[future_mask, "treatmentWindowCode"] = "FUTURE"
        limited.loc[future_mask, "recommendationCode"] = "RECOMMENDED"
        limited.loc[future_mask, "actionTypeCode"] = "TREAT"

        return SprayRecommendation.normalize_plant_protection_actions(limited)

    @staticmethod
    def _coerce_day(value: Any) -> datetime.date | None:
        if value in (None, ""):
            return None
        try:
            return pd.to_datetime(value).date()
        except Exception:
            return None

    @staticmethod
    def _normalize_recommendation_code(code: Any) -> str:
        text = str(code or "").upper()
        if text == "IRRIGATE":
            return "IRRIGATION"
        return text or SprayRecommendation.NO_ACTION_CODE

    @staticmethod
    def _extract_action_window(item: dict[str, Any]) -> tuple[datetime.date | None, datetime.date | None]:
        application_window = item.get("application_window") if isinstance(item.get("application_window"), dict) else {}
        start = SprayRecommendation._coerce_day(
            item.get("treatmentStartDate")
            or application_window.get("start_date")
            or item.get("recommendedIrrigationDate")
            or item.get("Date")
        )
        end = SprayRecommendation._coerce_day(
            item.get("treatmentEndDate")
            or application_window.get("end_date")
            or item.get("recommendedIrrigationDate")
            or item.get("Date")
        )
        if start and not end:
            end = start
        if end and not start:
            start = end
        return start, end

    @staticmethod
    def normalize_public_action_recommendations(
        actions: list[dict[str, Any]] | None,
        decision_date: datetime.date | str | None,
        *,
        mode: str = "resource",
    ) -> list[dict[str, Any]]:
        """Normalize public action rows without changing model-specific dosing fields."""
        decision_day = SprayRecommendation._coerce_day(decision_date)
        out: list[dict[str, Any]] = []
        for raw in actions or []:
            if not isinstance(raw, dict):
                continue
            item = deepcopy(raw)
            raw_window = str(item.get("treatmentWindowCode") or "").upper()
            if raw_window and raw_window not in SprayRecommendation.TIMING_WINDOW_CODES:
                item.setdefault("treatmentStageCode", raw_window)

            rec_code = SprayRecommendation._normalize_recommendation_code(item.get("recommendationCode"))
            raw_action = str(item.get("actionTypeCode") or "").upper()
            recorded_operation = rec_code.startswith("COTTON_APPLIED")
            no_action = rec_code == SprayRecommendation.NO_ACTION_CODE or raw_action == SprayRecommendation.NO_ACTION_CODE
            start, end = SprayRecommendation._extract_action_window(item)

            if no_action:
                item["recommendationCode"] = SprayRecommendation.NO_ACTION_CODE
                item["actionTypeCode"] = SprayRecommendation.NO_ACTION_CODE
                item["treatmentWindowCode"] = "NOT_PRESENT"
                item["treatmentStartDate"] = None
                item["treatmentEndDate"] = None
                out.append(item)
                continue

            item["recommendationCode"] = rec_code
            item["actionTypeCode"] = "TREAT"
            if start:
                item["treatmentStartDate"] = start.isoformat()
            if end:
                item["treatmentEndDate"] = end.isoformat()

            if decision_day is None:
                item["treatmentWindowCode"] = raw_window if raw_window in {"MISSED", "CURRENT", "FUTURE"} else "CURRENT"
            elif end and end < decision_day and recorded_operation:
                item["treatmentWindowCode"] = "CURRENT"
            elif end and end < decision_day:
                if mode == "plant_protection":
                    item["treatmentWindowCode"] = "MISSED"
                else:
                    item["recommendationCode"] = SprayRecommendation.NO_ACTION_CODE
                    item["actionTypeCode"] = SprayRecommendation.NO_ACTION_CODE
                    item["treatmentWindowCode"] = "NOT_PRESENT"
                    item["treatmentStartDate"] = None
                    item["treatmentEndDate"] = None
            elif start and start > decision_day:
                item["treatmentWindowCode"] = "FUTURE"
            else:
                item["treatmentWindowCode"] = "CURRENT"
            out.append(item)
        return out

    @staticmethod
    def spray_recommendation(
        disease_status,
        field_status,
        early_alert_days:int=4,
        spray_window_wide_define_by_curative_products:int = 5,
        minimum_recommendation_interval_days: int | None = None,
        max_recommendations_per_season: int | None = None,
    ):
        stress_code=get_disease_insect_weed_status_and_code(category='stress_code')
        stress_risks=disease_status[['Date','eppo_code','stress_risk']]
        field_risk_to_recommendationCode = {
            stress_code[1] : "NOT_NEEDED",
            stress_code[4]: "NOT_NEEDED",
            stress_code[2]: "NOT_NEEDED",
            stress_code[3]: "RECOMMENDED",
        }
        field_risk_to_actionTypeCode = {
            stress_code[1] : "NOT_NEEDED",
            stress_code[4]: "NOT_NEEDED",
            stress_code[2]: "NOT_NEEDED",
            stress_code[3]: "RECOMMENDED",
        }
        __dfsta__ = deepcopy(field_status)
        __dfsta__["recommendationCode"] = __dfsta__.field_risk.apply(
            lambda x: field_risk_to_recommendationCode[x]
        )
        __dfsta__["actionTypeCode"] = __dfsta__.field_risk.apply(
            lambda x: field_risk_to_actionTypeCode[x]
        )
        __dfsta__["treatmentWindowCode"] = "NOT_PRESENT"
        __dfsta__["treatmentStartDate"] = None
        __dfsta__["treatmentEndDate"] = None


        recommend_days = __dfsta__.loc[__dfsta__["recommendationCode"] == "RECOMMENDED", "Date"].values
        if len(recommend_days) > 0:  # logic for first spray
            spray_date_start_for_a_window = pd.to_datetime(recommend_days[0]).date()
            future_start = spray_date_start_for_a_window + datetime.timedelta(
                days=early_alert_days * -1
            )
            missing_start_date = spray_date_start_for_a_window + datetime.timedelta(
                days=spray_window_wide_define_by_curative_products
            )
            __dfsta__.loc[
                (__dfsta__.Date >= future_start)
                & (__dfsta__.Date < spray_date_start_for_a_window),
                "treatmentWindowCode",
            ] = "FUTURE"
            __dfsta__.loc[
                (__dfsta__.Date >= future_start)
                & (__dfsta__.Date < spray_date_start_for_a_window),
                "treatmentStartDate",
            ] = spray_date_start_for_a_window
            __dfsta__.loc[
                (__dfsta__.Date >= future_start)
                & (__dfsta__.Date < spray_date_start_for_a_window),
                "treatmentEndDate",
            ] = missing_start_date
            if not (
                "PROTECTED"
                in __dfsta__.loc[
                    (__dfsta__.Date >= future_start)
                    & (__dfsta__.Date < missing_start_date),
                    "field_risk",
                ].values
            ):
                __dfsta__.loc[
                    (__dfsta__.Date >= future_start)
                    & (__dfsta__.Date < spray_date_start_for_a_window),
                    "recommendationCode",
                ] = "RECOMMENDED"
                __dfsta__.loc[
                    (__dfsta__.Date >= future_start)
                    & (__dfsta__.Date < spray_date_start_for_a_window),
                    "actionTypeCode",
                ] = "TREAT"

            __dfsta__.loc[
                (__dfsta__.Date >= spray_date_start_for_a_window)
                & (__dfsta__.Date < missing_start_date),
                "treatmentWindowCode",
            ] = "CURRENT"
            __dfsta__.loc[
                (__dfsta__.Date >= spray_date_start_for_a_window)
                & (__dfsta__.Date < missing_start_date),
                "treatmentStartDate",
            ] = spray_date_start_for_a_window
            __dfsta__.loc[
                (__dfsta__.Date >= spray_date_start_for_a_window)
                & (__dfsta__.Date < missing_start_date),
                "treatmentEndDate",
            ] = missing_start_date

            (
                missing_end_date_for_a_spray_window,
                next_spray,
                field_risks_after_missing,
                next_spray_start_date,
            ) = SprayRecommendation.get_missing_end_date_for_a_spray_window(missing_start_date + datetime.timedelta(days=-1),stress_risks=stress_risks)
            if len(field_risks_after_missing) > 0:
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "treatmentWindowCode",
                ] = "MISSED"
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "treatmentStartDate",
                ] = spray_date_start_for_a_window
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "treatmentEndDate",
                ] = missing_start_date
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "recommendationCode",
                ] = "NECESSARY"  # https://jira.digital-farming.com/browse/DFITE-28094
            while next_spray:

                field_risks_after_missing[
                    "recommendationCode"
                ] = field_risks_after_missing.field_risk.apply(
                    lambda x: field_risk_to_recommendationCode[x]
                )
                field_risks_after_missing[
                    "actionTypeCode"
                ] = field_risks_after_missing.field_risk.apply(
                    lambda x: field_risk_to_actionTypeCode[x]
                )
                __dfsta__.loc[__dfsta__.Date>= field_risks_after_missing.Date.min(),"recommendationCode",] = field_risks_after_missing.recommendationCode.values
                __dfsta__.loc[
                    __dfsta__.Date
                    >= field_risks_after_missing.Date.min(),
                    "actionTypeCode",
                ] = field_risks_after_missing.actionTypeCode.values

                spray_date_start_for_a_window = next_spray_start_date
                future_start = spray_date_start_for_a_window + datetime.timedelta(
                    days=early_alert_days * -1
                )
                missing_start_date = spray_date_start_for_a_window + datetime.timedelta(
                    days=spray_window_wide_define_by_curative_products
                )

                if (
                    future_start
                    < __dfsta__.loc[
                        __dfsta__.treatmentWindowCode == "CURRENT", "Date"
                    ].max()
                ):
                    future_start = __dfsta__.loc[
                        __dfsta__.treatmentWindowCode == "CURRENT", "Date"
                    ].max() + datetime.timedelta(days=1)

                __dfsta__.loc[
                    (__dfsta__.Date >= future_start)
                    & (__dfsta__.Date < spray_date_start_for_a_window),
                    "treatmentWindowCode",
                ] = "FUTURE"
                __dfsta__.loc[
                    (__dfsta__.Date >= future_start)
                    & (__dfsta__.Date < spray_date_start_for_a_window),
                    "treatmentStartDate",
                ] = spray_date_start_for_a_window
                # ] = spray_date_start_for_a_window
                __dfsta__.loc[
                    (__dfsta__.Date >= future_start)
                    & (__dfsta__.Date < spray_date_start_for_a_window),
                    "treatmentEndDate",
                ] = missing_start_date
                if not (
                    "PROTECTED"
                    in __dfsta__.loc[
                        (__dfsta__.Date >= future_start)
                        & (__dfsta__.Date < missing_start_date),
                        "field_risk",
                    ].values
                ):
                    __dfsta__.loc[
                        (__dfsta__.Date >= future_start)
                        & (__dfsta__.Date < spray_date_start_for_a_window),
                        "recommendationCode",
                    ] = "RECOMMENDED"
                    __dfsta__.loc[
                        (__dfsta__.Date >= future_start)
                        & (__dfsta__.Date < spray_date_start_for_a_window),
                        "actionTypeCode",
                    ] = "TREAT"

                __dfsta__.loc[
                    (__dfsta__.Date >= spray_date_start_for_a_window)
                    & (__dfsta__.Date < missing_start_date),
                    "treatmentWindowCode",
                ] = "CURRENT"
                __dfsta__.loc[
                    (__dfsta__.Date >= spray_date_start_for_a_window)
                    & (__dfsta__.Date < missing_start_date),
                    "treatmentStartDate",
                ] = spray_date_start_for_a_window
                __dfsta__.loc[
                    (__dfsta__.Date >= spray_date_start_for_a_window)
                    & (__dfsta__.Date < missing_start_date),
                    "treatmentEndDate",
                ] = missing_start_date
                if (
                    missing_start_date + datetime.timedelta(days=-1)
                    >= stress_risks.Date.max()
                ):
                    break
                else:
                    (
                        missing_end_date_for_a_spray_window,
                        next_spray,
                        field_risks_after_missing,
                        next_spray_start_date,
                    ) =SprayRecommendation.get_missing_end_date_for_a_spray_window(missing_start_date + datetime.timedelta(days=-1),stress_risks=stress_risks)

                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "treatmentWindowCode",
                ] = "MISSED"
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "treatmentStartDate",
                ] = spray_date_start_for_a_window
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "treatmentEndDate",
                ] = missing_start_date
                __dfsta__.loc[
                    (__dfsta__.Date >= missing_start_date)
                    & (__dfsta__.Date <= missing_end_date_for_a_spray_window),
                    "recommendationCode",
                ] = "NECESSARY"
        __dfsta__ = SprayRecommendation.normalize_plant_protection_actions(__dfsta__)
        __dfsta__ = SprayRecommendation._limit_recommendation_windows(
            __dfsta__,
            minimum_recommendation_interval_days=minimum_recommendation_interval_days,
            max_recommendations_per_season=max_recommendations_per_season,
        )
        __dfsta__ = SprayRecommendation.normalize_plant_protection_actions(__dfsta__)
        return __dfsta__[["Date","recommendationCode","actionTypeCode","treatmentWindowCode","treatmentStartDate","treatmentEndDate"]] 
    @staticmethod
    def stress_protection_times_cal(applied_fungicide:pd.DataFrame=None,request_day:datetime.date=None,disease_eppos:List[str]=None)->pd.DataFrame:
        _stress_protection_times:pd.DataFrame = pd.DataFrame(columns=["Date", "diseaseId", "protectionEndDate"])
        if len(applied_fungicide) == 0:
            return _stress_protection_times
        for ds in disease_eppos:
            __protection_end_date = None
            for ind, app in applied_fungicide.iterrows():
                __app_date = app["applied_date"]

                if ds == app.stress:
                    if __protection_end_date is None:
                        __protection_end_date = max(
                            [
                                __app_date
                                + datetime.timedelta(days=app.curative_protection_days),
                                __app_date
                                + datetime.timedelta(
                                    days=app.preventive_protection_days
                                ),
                            ]
                        )
                    else:
                        __protection_end_date = max(
                            [
                                __protection_end_date,
                                __app_date
                                + datetime.timedelta(days=app.curative_protection_days),
                                __app_date
                                + datetime.timedelta(
                                    days=app.preventive_protection_days
                                ),
                            ]
                        )
            if __protection_end_date != None:
                _stress_protection_times.loc[len(_stress_protection_times)] = [
                    request_day,
                    ds,
                    __protection_end_date.date(),
                ]
            else:
                _stress_protection_times.loc[len(_stress_protection_times)] = [
                    request_day,
                    ds,
                    None,
                ]
        return _stress_protection_times

class FungicideEffects:
    @staticmethod
    def effect_from_applied_fungicide(
        applied_fungicides: list[dict] = None,
        target: str = None,
        latent_days: int = None,
        preventive_efficacy: float = 0.95,
        curative_efficacy: float = 0.5,
        eradicative_efficacy_value: float = 0.5,
        eradicant_protection_days: int = 5,
        reference_date: datetime.date = None
    ) -> tuple[float, float, float, float]:
        """
        Calculate fungicide effects on infection risk reduction.
        Returns (overall, preventive, curative, eradicative), each ∈ [0,1].
        """

        if not applied_fungicides:
            return 1, 1, 1, 1

        # Ensure reference_date is a datetime.date
        if isinstance(reference_date, pd.Timestamp):
            reference_date = reference_date.date()
        elif isinstance(reference_date, datetime.datetime):
            reference_date = reference_date.date()

        overall, preventive, curative, eradicative = 1, 1, 1, 1
        all_effects = []

        for af in applied_fungicides:
            app_date = af["applied_date"]

            # Normalize applied_date
            if isinstance(app_date, pd.Timestamp):
                app_date = app_date.date()
            elif isinstance(app_date, datetime.datetime):
                app_date = app_date.date()

            # Preventive effect
            if af.get("preventive_protection_days", 0) > 0:
                if app_date <= reference_date < app_date + datetime.timedelta(days=af["preventive_protection_days"]):
                    preventive = 1 - preventive_efficacy
                all_effects.append(preventive)

            # Eradicative effect (binary)
            if af.get("eradicative_efficacy", False):
                if app_date <= reference_date < app_date + datetime.timedelta(days=eradicant_protection_days):
                    eradicative = 1 - eradicative_efficacy_value
                all_effects.append(eradicative)

            # Curative effect
            if af.get("curative_protection_days", 0) > 0:
                curative_days = af["curative_protection_days"]
                curative_start_days = min(0, latent_days - curative_days) if latent_days else 0
                start = app_date + datetime.timedelta(days=curative_start_days)
                end   = start + datetime.timedelta(days=curative_days)
                if start <= reference_date < end:
                    curative = 1 - curative_efficacy
                all_effects.append(curative)

        if not all_effects:
            return overall, preventive, curative, eradicative

        overall = min(all_effects)
        return overall, preventive, curative, eradicative
    @staticmethod
    def fungicide_spray_number_correction_for_target_until_refdate(applied_fungicides:pd.DataFrame=None,target:str=None,
                                                                reference_date:datetime.date=None,spray_num_correction:Dict=None)->int:
        if applied_fungicides is None:
            return 1        
        if len(applied_fungicides)==0:
            return 1     
        dfaf=deepcopy(applied_fungicides)
        dfaf.applied_date=pd.to_datetime(dfaf.applied_date)
        dfaf=dfaf.loc[dfaf.stress==target,:]
        #if target specific fungicide is empty
        if len(dfaf)==0:
            return 1
        if 'method' in dfaf.columns:
            dfaf=dfaf[(dfaf.method!='SEED_BOX')&(dfaf.method!='SEED')]
        if len(dfaf)==0:
            return 1
        dfaf=dfaf[dfaf.applied_date<=reference_date]
        dfaf=dfaf[['stress','applied_date']].drop_duplicates()
        return np.interp(len(dfaf),spray_num_correction['Spray_num'],spray_num_correction['Correction'])
        
        

from dataclasses import dataclass
import datetime as _dt
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
@dataclass(frozen=True)
class _KillParts:
    base: float
    residual: float
    rainfast: float
    temp: float
    instar: float

    @property
    def total(self) -> float:
        val = self.base * self.residual * self.rainfast * self.temp * self.instar
        return float(np.clip(val, 0.0, 1.0))



class InsecticideEffects:
    # ---------- normalization helpers ----------
    @staticmethod
    def _normalize_app_date(val: Any) -> Optional[_dt.date]:
        if val is None:
            return None
        if isinstance(val, pd.Timestamp):
            return val.date()
        if isinstance(val, _dt.datetime):
            return val.date()
        if isinstance(val, _dt.date):
            return val
        try:
            return pd.to_datetime(val).date()
        except Exception:
            return None

    @staticmethod
    def _get_app_date(app: Dict) -> Optional[_dt.date]:
        for k in ("applied_date", "date", "Date", "appliedAt", "applied_at"):
            if k in app:
                d = InsecticideEffects._normalize_app_date(app[k])
                if d:
                    return d
        return None

    @staticmethod
    def _get_product_bundle(app: Dict) -> Dict:
        return app["pesticide"] if isinstance(app.get("pesticide"), dict) else app

    # ---------- unit conversion ----------
    @staticmethod
    def _ai_g_per_ml(prod: Dict) -> float:
        g_per_L = float((prod.get("ai_content_g_per_L") or 0.0))
        return g_per_L / 1000.0 if g_per_L > 0 else 0.0

    @staticmethod
    def _to_g_ai_ha(value: float, unit: str, prod: Dict) -> float:
        """Convert a dose-like value to g a.i./ha using product AI content when needed."""
        unit = (unit or "").lower().strip()
        g_per_ml = InsecticideEffects._ai_g_per_ml(prod)

        if unit == "g_ai_ha":
            return float(value)
        if unit == "g_ai_mu":
            return float(value) * 15.0  # 1 ha = 15 mu
        if unit == "ml_ai_ha":
            return float(value) * g_per_ml
        if unit == "ml_ai_mu":
            return float(value) * g_per_ml * 15.0
        # unknown: give up (safe zero)
        return 0.0

    @staticmethod
    def _dose_to_g_ai_ha(app: Dict) -> float:
        prod = InsecticideEffects._get_product_bundle(app)
        return InsecticideEffects._to_g_ai_ha(
            float(app.get("dose_value", 0.0) or 0.0),
            app.get("dose_unit"),
            prod,
        )

    @staticmethod
    def _ed50_to_g_ai_ha(app: Dict, ED50_raw: float, ed50_unit_hint: Optional[str]) -> float:
        """
        Convert ED50 from the same 'dose unit family' as the label/rb into g a.i./ha.
        If ed50_unit_hint is missing, try product-level 'dose_unit'; else assume already g_ai_ha.
        """
        prod = InsecticideEffects._get_product_bundle(app)
        unit = (ed50_unit_hint or prod.get("dose_unit") or "").lower().strip()
        if not unit:
            # defensive: assume already g_ai_ha
            return float(ED50_raw)
        return InsecticideEffects._to_g_ai_ha(float(ED50_raw), unit, prod)

    # ---------- effect modifiers ----------
    @staticmethod
    def _dose_response_kill(dose: float, Emax: float, ED50: float, hill: float) -> float:
        if dose <= 0.0 or Emax <= 0.0 or ED50 <= 0.0 or hill <= 0.0:
            return 0.0
        denom = (ED50 ** hill) + (dose ** hill)
        if denom <= 0:
            return 0.0
        return float(np.clip(Emax * (dose ** hill) / denom, 0.0, 1.0))

    @staticmethod
    def _rainfast_penalty(app_date: _dt.date, hourly_weather: Optional[pd.DataFrame], app: Dict) -> float:
        prod = InsecticideEffects._get_product_bundle(app)
        rf = (prod.get("rainfast") or {})
        hours = float(rf.get("rainfast_hours", 0.0) or 0.0)
        thresh = float(rf.get("rainfast_threshold_mm", np.inf) or np.inf)
        penalty = float(rf.get("rain_penalty", 1.0) or 1.0)
        if hours <= 0 or hourly_weather is None or hourly_weather.empty:
            return 1.0
        start = pd.to_datetime(app_date)
        end = start + pd.Timedelta(hours=hours)
        sub = hourly_weather[(hourly_weather["DateTime"] >= start) & (hourly_weather["DateTime"] < end)]
        if sub.empty:
            return 1.0
        if "precipitation" not in sub.columns:
            raise ValueError("hourly weather missing required field: precipitation")
        precip = pd.to_numeric(sub["precipitation"], errors="coerce")
        if precip.isna().any():
            raise ValueError("hourly weather.precipitation has missing values")
        rain = float(precip.sum())
        return penalty if rain >= thresh else 1.0

    @staticmethod
    def _temp_q10_adjustment(day_temp_mean: float, app: Dict) -> float:
        prod = InsecticideEffects._get_product_bundle(app)
        tmod = (prod.get("temp_modifier") or {})
        if not tmod.get("enabled", False):
            return 1.0
        ref_T = float(tmod.get("ref_T", 25.0) or 25.0)
        Q10 = float(tmod.get("Q10", 1.0) or 1.0)
        if Q10 <= 0:
            return 1.0
        return float(np.clip(Q10 ** ((float(day_temp_mean) - ref_T) / 10.0), 0.5, 1.5))

    @staticmethod
    def _residual_decay(days_since: float, half_life_days: float) -> float:
        if half_life_days is None or half_life_days <= 0.0:
            return 1.0 if days_since <= 0.0 else 0.0
        return float(0.5 ** (max(days_since, 0.0) / half_life_days))

    @staticmethod
    def _instar_weight(instar_distribution: Optional[Dict[str, float]], instar_susc: Optional[Dict[str, float]]) -> float:
        if not instar_distribution or not instar_susc:
            return 1.0
        w = 0.0
        for k, p in instar_distribution.items():
            w += float(p) * float(instar_susc.get(k, 1.0))
        return float(np.clip(w, 0.1, 1.2))

    # ---------- main API ----------
    @staticmethod
    def effective_kill_on_date(
        applied_insecticides: Union[List[Dict], pd.DataFrame, None],
        reference_date: Union[_dt.date, _dt.datetime, pd.Timestamp],
        hourly_weather: Optional[pd.DataFrame] = None,
        protection_threshold: float = 0.40,
        pesticide_master: Optional[Dict[str, Dict]] = None,
        instar_distribution_today: Optional[Dict[str, float]] = None,
        instar_susceptibility: Optional[Dict[str, float]] = None,
    ) -> Tuple[float, bool]:
        """
        Returns: (max_effective_kill_today, is_protected)
        - max_effective_kill_today: includes residual (not only D0)
        - is_protected: True if (kill_today >= threshold) OR (label_protect >= threshold)
        """
        
        dfw = None
        day_temp_mean = 25.0
        if isinstance(hourly_weather, pd.DataFrame) and not hourly_weather.empty and "DateTime" in hourly_weather.columns:
            dfw = hourly_weather
            day_rows = dfw[pd.to_datetime(dfw["DateTime"]).dt.date == reference_date]
            if not day_rows.empty and "temperature_2m" in day_rows.columns:
                day_temp_mean = float(day_rows["temperature_2m"].mean())

        apps = applied_insecticides.to_dict("records") if isinstance(applied_insecticides, pd.DataFrame) else applied_insecticides

        max_kill_today = 0.0
        max_label_protect = 0.0

        for app in apps:
            app_date = app.get('date')
            if app_date is None or reference_date < app_date:
                continue

            prod = InsecticideEffects._get_product_bundle(app)

            # 1) DR parameters (prefer embedded; fallback to master)
            dr = (prod.get("dose_response") or {})
            Emax = dr.get("Emax")
            ED50_raw = dr.get("ED50")
            hill = dr.get("hill", 1.2)

            # If needed, fall back to master data
            if (Emax is None or ED50_raw is None) and pesticide_master and app.get("product_key") in pesticide_master:
                m = pesticide_master[app["product_key"]]
                Emax = m.get("dose_response", {}).get("Emax", Emax)
                ED50_raw = m.get("dose_response", {}).get("ED50", ED50_raw)
                hill = m.get("dose_response", {}).get("hill", hill)

            # Convert to float safely
            Emax = float(Emax) if Emax is not None else np.nan
            ED50_raw = float(ED50_raw) if ED50_raw is not None else np.nan
            hill = float(hill)

            # 2) Convert both dose and ED50 into the SAME unit: g a.i./ha
            dose_gha = InsecticideEffects._dose_to_g_ai_ha(app)
            ed50_gha = InsecticideEffects._ed50_to_g_ai_ha(app, ED50_raw, prod.get("dose_unit"))

            # 3) Base kill (if we have sensible params)
            base_kill = InsecticideEffects._dose_response_kill(dose_gha, Emax, ed50_gha, hill)

            # 4) Residual × rainfast × temp Q10 × instar weighting
            half_life_days = float((prod.get("residual", {}) or {}).get("half_life_days", 0.0) or 0.0)
            days_since = (reference_date - app_date).days
            resid = InsecticideEffects._residual_decay(days_since, half_life_days)
            rpen = InsecticideEffects._rainfast_penalty(app_date, dfw, app)
            tq10 = InsecticideEffects._temp_q10_adjustment(day_temp_mean, app)
            iw = InsecticideEffects._instar_weight(instar_distribution_today, instar_susceptibility)
            
            kill_today = float(np.clip(base_kill * resid * rpen * tq10 * iw, 0.0, 1.0))
            label_protect = float(np.clip(resid * rpen * tq10, 0.0, 1.0))
            
            # Safety net: if DR inputs failed (e.g., unit mismatch left ED50 invalid),
            # use label protection × Emax as a lower-bound kill proxy so it’s not 0.
            if (kill_today == 0.0 or not np.isfinite(kill_today)) and np.isfinite(Emax):
                kill_today = float(np.clip(label_protect * 0.7 * max(Emax, 0.0), 0.0, 1.0))
            # print(f"App {app.get('product_key','?')} on {app_date}: dose {dose_gha:.2f} g_ai/ha, ED50 {ed50_gha:.2f} g_ai/ha, base {base_kill:.2f}, resid {resid:.2f}, rainfast {rpen:.2f}, temp {tq10:.2f}, instar {iw:.2f} => kill_today {kill_today:.2f}, label_protect {label_protect:.2f},half_life_days{half_life_days:.2f},base_kill{base_kill:.2f}")
            if kill_today > max_kill_today:
                max_kill_today = kill_today
            if label_protect > max_label_protect:
                max_label_protect = label_protect

        is_protected = bool((max_kill_today >= protection_threshold) or (max_label_protect >= protection_threshold))

        return max_kill_today, is_protected
