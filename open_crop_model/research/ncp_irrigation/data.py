"""Traceable observation normalization, whole-season splits and exact alignment."""

from pathlib import Path
import hashlib
import pandas as pd
import numpy as np

IDENTIFIERS = ["site_id", "experiment_id", "season_id", "treatment_id", "replicate_id"]
OBSERVATION_COLUMNS = IDENTIFIERS + [
    "date",
    "variable",
    "value",
    "unit",
    "depth_top_cm",
    "depth_bottom_cm",
    "irrigation_method",
    "measurement_method",
    "qc_flag",
    "source_id",
]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def quality_control_observations(frame):
    if 'measurement_method' in frame:
        from .et_observations import classify_et_targets
        out=classify_et_targets(frame)
    else:
        out = frame.copy()
    if "qc_flag" not in out:
        out["qc_flag"] = "pass"
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    dates = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    out.loc[dates.isna(), "qc_flag"] = "invalid_date"
    out.loc[~np.isfinite(out.value), "qc_flag"] = "missing_or_nonfinite"
    out.loc[out.variable.eq("et_mm") & out.value.lt(0), "qc_flag"] = "negative_et"
    out.loc[
        out.variable.eq("soil_theta")
        & np.isfinite(out.value)
        & ~out.value.between(0, 1),
        "qc_flag",
    ] = "theta_outside_physical_range"
    keys = [
        c
        for c in IDENTIFIERS
        + [
            "date",
            "variable",
            "depth_top_cm",
            "depth_bottom_cm",
            "spatial_support",
            "phenology_event",
            "window_start",
            "window_end",
        ]
        if c in out
    ]
    for _, group in out.groupby(keys, dropna=False, sort=False):
        if len(group) > 1:
            if group.value.nunique(dropna=False) > 1:
                out.loc[group.index, "qc_flag"] = "conflicting_duplicate"
            else:
                out.loc[group.index[1:], "qc_flag"] = "duplicate_copy"
    return out


def freeze_split(seasons):
    out = seasons.copy()
    out["split"] = "excluded"
    if out.empty:
        return out
    eligible = out[out.eligible.astype(bool)].copy()
    if len(eligible[["site_id", "experiment_id", "season_id"]].drop_duplicates()) < 6:
        out.loc[out.eligible.astype(bool), "exclusion_reason"] = (
            "fewer_than_six_eligible_seasons"
        )
        return out
    # Source-group ID spans all reused publications and all treatments/repeats.
    groups = eligible.groupby("source_group_id").start_date.min().sort_values()
    cut = int(len(groups) * 0.6)
    train = set(groups.index[:cut])
    valid = set(groups.index[cut:])
    if not {"wheat", "maize"} <= set(
        eligible[eligible.source_group_id.isin(train)].crop
    ) or not {"wheat", "maize"} <= set(
        eligible[eligible.source_group_id.isin(valid)].crop
    ):
        out.loc[out.eligible.astype(bool), "exclusion_reason"] = (
            "calibration_and_validation_must_each_contain_both_crops"
        )
        return out
    for group_id, group in out.groupby("source_group_id", sort=False):
        if not group.eligible.astype(bool).all():
            out.loc[group.index, "exclusion_reason"] = (
                "source_group_contains_ineligible_season"
            )
            continue
        out.loc[group.index, "split"] = (
            "calibration"
            if group_id in train
            else "validation"
            if group_id in valid
            else "excluded"
        )
    return out


def align_daily(predictions, observations, keys=None):
    keys = keys or ["date"]
    if predictions.duplicated(keys).any() or observations.duplicated(keys).any():
        raise ValueError(
            "Alignment keys must be unique after QC and explicit aggregation"
        )
    return predictions.merge(
        observations,
        on=keys,
        how="inner",
        suffixes=("_predicted", "_observed"),
        validate="one_to_one",
    )


def depth_average(theta, thickness_mm, top_cm, bottom_cm):
    if len(theta) != len(thickness_mm) or top_cm < 0 or bottom_cm < top_cm:
        raise ValueError("Invalid depth operator")
    top = top_cm * 10
    bottom = bottom_cm * 10
    depth = 0.0
    integral = covered = 0.0
    if top == bottom:
        for value, thickness in zip(theta, thickness_mm):
            if depth <= top < depth + thickness:
                return value
            depth += thickness
        raise ValueError("Point lies outside simulated profile")
    for value, thickness in zip(theta, thickness_mm):
        overlap = max(0.0, min(bottom, depth + thickness) - max(top, depth))
        depth += thickness
        integral += value * overlap
        covered += overlap
    if abs(covered - (bottom - top)) > 1e-9:
        raise ValueError("Observed depth exceeds simulated profile")
    return integral / covered


def _dates(frame):
    if "日期" in frame:
        return pd.to_datetime(
            frame["日期"].astype(str).str.strip(), format="mixed", errors="coerce"
        )
    return pd.to_datetime(
        {"year": frame["年"], "month": frame["月"], "day": frame["日"]}, errors="coerce"
    )


def audit_local_data(data_root, output_directory):
    """Import available Yucheng observations without inventing management closure."""
    root = Path(data_root).parent
    dest = Path(output_directory)
    dest.mkdir(parents=True, exist_ok=True)
    registry = pd.read_csv(Path(__file__).with_name("dataset_registry.csv"))
    qc = []
    observations = []
    weather = pd.DataFrame()
    seasons = []
    source_rows = []
    for source in registry.to_dict("records"):
        path = root / source["path"]
        entry = {**source, "exists": path.exists()}
        if not path.exists():
            qc.append(
                {
                    "source_id": source["source_id"],
                    "issue": "missing_source",
                    "count": 1,
                    "detail": source["path"],
                }
            )
            source_rows.append(entry)
            continue
        entry["actual_sha256"] = sha256(path)
        entry["checksum_matches"] = entry["actual_sha256"] == source["sha256"]
        source_rows.append(entry)
        if not entry["checksum_matches"]:
            qc.append(
                {
                    "source_id": source["source_id"],
                    "issue": "source_checksum_changed",
                    "count": 1,
                    "detail": source["path"],
                }
            )
            continue
        name = source["path"]
        if "/yucheng/" not in name or not any(
            x in name
            for x in (
                "气象观测数据",
                "辐射观测数据",
                "蒸散发量",
                "土壤含水量",
                "生育期观测",
                "YCAZH01冬小麦",
                "YCAZH01夏玉米",
            )
        ):
            continue
        frame = pd.read_csv(path, encoding="utf-8-sig")
        if "生育期观测" in name:
            crop = "wheat" if "冬小麦" in name else "maize"
            for row in frame.to_dict("records"):
                first = pd.to_datetime(
                    str(row["播种期"]).strip(), format="mixed", errors="coerce"
                )
                last = pd.to_datetime(
                    str(row["收获期"]).strip(), format="mixed", errors="coerce"
                )
                year = int(row["年"])
                sid = f"{crop}-{year}"
                seasons.append(
                    dict(
                        site_id="YCA",
                        experiment_id=str(row["样地代码"]),
                        season_id=sid,
                        treatment_id="historical_unknown",
                        source_group_id=f"YCA-historical-{year}",
                        crop=crop,
                        cultivar=str(row["作物品种"]),
                        start_date=first.date().isoformat() if pd.notna(first) else "",
                        end_date=last.date().isoformat() if pd.notna(last) else "",
                        eligible=False,
                        exclusion_reason="irrigation_calendar_and_metering_unconfirmed; soil_hydraulic_profile_missing; plot_to_sensor_alignment_unconfirmed",
                    )
                )
            continue
        dates = _dates(frame)
        if "气象观测数据" in name:
            mapping = {
                "日最低空气温度": "tmin_c",
                "日最高空气温度": "tmax_c",
                "日降水量": "precipitation_mm",
                "日平均空气相对湿度": "relative_humidity_pct",
                "日平均风速": "wind_speed_m_s",
            }
            weather = pd.DataFrame({"date": dates.dt.strftime("%Y-%m-%d")})
            for raw, canonical in mapping.items():
                weather[canonical] = pd.to_numeric(frame[raw], errors="coerce")
            # Measurement height and radiation units must be confirmed before PM.
            weather["wind_height_status"] = "unconfirmed"
            qc.append(
                {
                    "source_id": source["source_id"],
                    "issue": "weather_missing_numeric_cells",
                    "count": int(weather[list(mapping.values())].isna().sum().sum()),
                    "detail": "No temporal gap filling",
                }
            )
            continue
        if "辐射观测数据" in name:
            radiation = pd.DataFrame(
                {
                    "date": dates.dt.strftime("%Y-%m-%d"),
                    "reported_solar_radiation": pd.to_numeric(
                        frame["总辐射"], errors="coerce"
                    ),
                }
            )
            if not weather.empty:
                weather = weather.merge(
                    radiation, on="date", how="left", validate="one_to_one"
                )
            qc.append(
                {
                    "source_id": source["source_id"],
                    "issue": "radiation_unit_confirmation_required",
                    "count": len(frame),
                    "detail": "Reported radiation preserved; no automatic J/MJ assumption",
                }
            )
            continue
        variable = (
            "et_mm"
            if "蒸散发量" in name
            else "soil_theta"
            if "土壤含水量" in name
            else "yield_kg_ha"
        )
        rawvar = (
            "蒸散总量"
            if variable == "et_mm"
            else "土壤含水量"
            if "中子仪" in name
            else "土壤体积含水量"
            if variable == "soil_theta"
            else "籽粒干重"
        )
        method = (
            "lysimeter"
            if variable == "et_mm"
            else "neutron"
            if "中子仪" in name
            else "TDR"
            if variable == "soil_theta"
            else "quadrat"
        )
        factor = 0.01 if method == "neutron" else 10.0 if method == "quadrat" else 1.0
        unit = (
            "mm/day"
            if variable == "et_mm"
            else "m3/m3"
            if variable == "soil_theta"
            else "kg/ha"
        )
        # Conversions are provisional until metadata units and sampling basis are confirmed.
        for i, row in frame.iterrows():
            if pd.isna(dates.iloc[i]):
                continue
            plot = str(
                row.get(
                    "样地代码",
                    row.get("观测设施代码", row.get("观测设施代码", "unknown")),
                )
            )
            observations.append(
                dict(
                    site_id="YCA",
                    experiment_id=plot,
                    season_id="unmatched",
                    treatment_id="historical_unknown",
                    replicate_id=str(row.get("样方号", "aggregate")),
                    date=dates.iloc[i].date().isoformat(),
                    variable=variable,
                    value=pd.to_numeric(row[rawvar], errors="coerce") * factor,
                    unit=unit,
                    depth_top_cm=row.get("深度", 0),
                    depth_bottom_cm=row.get("深度", 0),
                    irrigation_method="unknown",
                    measurement_method=method,
                    qc_flag="unit_or_sampling_basis_unconfirmed",
                    source_id=source["source_id"],
                )
            )
    obs = (
        quality_control_observations(
            pd.DataFrame(observations, columns=OBSERVATION_COLUMNS)
        )
        if observations
        else pd.DataFrame(columns=OBSERVATION_COLUMNS)
    )
    if not obs.empty:
        for flag, count in obs.qc_flag.value_counts().items():
            qc.append(
                {
                    "source_id": "YCA-observations",
                    "issue": flag,
                    "count": int(count),
                    "detail": "Flagged records retained; no averaging of conflicts",
                }
            )
    columns = [
        "site_id",
        "experiment_id",
        "season_id",
        "treatment_id",
        "source_group_id",
        "crop",
        "cultivar",
        "start_date",
        "end_date",
        "eligible",
        "exclusion_reason",
    ]
    split = freeze_split(pd.DataFrame(seasons, columns=columns))
    obs.to_csv(dest / "observations.csv", index=False)
    weather.to_csv(dest / "weather_reported.csv", index=False)
    split.to_csv(dest / "validation_split.csv", index=False)
    pd.DataFrame(source_rows).to_csv(dest / "source_checksums.csv", index=False)
    irrigation_path = (
        Path(data_root) / "curated/irrigation/station_irrigation_events_wheat_maize.csv"
    )
    if irrigation_path.exists():
        irrigation = pd.read_csv(irrigation_path, encoding="utf-8-sig")
        # Record conflicts. Do not silently rewrite a disputed year/date.
        irrigation["qc_flag"] = "metering_and_calendar_completeness_unconfirmed"
        dates = pd.to_datetime(irrigation.event_date, errors="coerce")
        conflict = irrigation.crop_original.astype(str).str.contains(
            "玉米"
        ) & dates.dt.year.ne(pd.to_numeric(irrigation.reported_year, errors="coerce"))
        irrigation.loc[conflict, "qc_flag"] = "reported_year_date_conflict"
        irrigation.to_csv(dest / "irrigation_events_reported.csv", index=False)
        qc.append(
            {
                "source_id": "station-irrigation",
                "issue": "reported_year_date_conflict",
                "count": int(conflict.sum()),
                "detail": "No automatic correction",
            }
        )
        qc.append(
            {
                "source_id": "station-irrigation",
                "issue": "metering_location_unconfirmed",
                "count": len(irrigation),
                "detail": "No zero irrigation inferred from missing records",
            }
        )
    omitted = (
        Path(data_root) / "documentation/paper_review/omitted_irrigation_records.csv"
    )
    if omitted.exists():
        rows = pd.read_csv(omitted)
        rows.to_csv(dest / "irrigation_omitted_records.csv", index=False)
        qc.append(
            {
                "source_id": "station-irrigation",
                "issue": "omitted_event_candidates",
                "count": len(rows),
                "detail": "Quarantined until plot/date reconciliation; originals unchanged",
            }
        )
    if not seasons:
        qc.append(
            {
                "source_id": "local-data",
                "issue": "no_eligible_drivers",
                "count": 0,
                "detail": "Canonical verified cases must be supplied",
            }
        )
    return (
        obs,
        split,
        pd.DataFrame(qc, columns=["source_id", "issue", "count", "detail"]),
        source_rows,
    )
