"""Reproducible validation; observational gates fail closed without verified cases."""

from pathlib import Path
from copy import deepcopy
from datetime import date, timedelta, datetime, timezone
import argparse, ast, json, platform, subprocess, sys
import pandas as pd
from .data import audit_local_data, sha256
from .metrics import interval_summary
from .calibration import (
    freeze_parameters,
    load_frozen_parameters,
    joint_samples,
    calibrate_candidates,
    grouped_objective,
    check_observation_independence,
)
from .model import simulate_season

OUTPUT_COLUMNS = {
    "daily_predictions": [
        "site_id",
        "experiment_id",
        "season_id",
        "treatment_id",
        "source_group_id",
        "date",
        "crop",
        "method",
        "variable",
        "predicted",
        "observed",
        "qc_flag",
        "evidence",
    ],
    "water_balance": [
        "case_id",
        "structure",
        "evidence",
        "date",
        "precipitation_mm",
        "irrigation_field_mm",
        "capillary_rise_mm",
        "soil_evaporation_mm",
        "transpiration_mm",
        "canopy_evaporation_mm",
        "application_evaporation_mm",
        "runoff_mm",
        "off_field_drift_mm",
        "bottom_drainage_mm",
        "storage_change_mm",
        "balance_residual_mm",
    ],
    "metrics_by_group": [
        "site_id",
        "crop",
        "method",
        "variable",
        "source_group_id",
        "split",
        "n",
        "rmse",
        "mae",
        "bias",
        "nrmse",
        "relative_bias",
        "status",
        "acceptance",
    ],
    "treatment_contrasts": [
        "source_group_id",
        "method_a",
        "method_b",
        "variable",
        "observed_difference",
        "observed_ci_low",
        "observed_ci_high",
        "predicted_difference",
        "status",
    ],
    "parameter_identifiability": ["parameter", "status", "detail"],
    "ablation_results": [
        "case_id",
        "structure",
        "evidence",
        "et_mm",
        "yield_kg_ha",
        "bottom_drainage_mm",
        "balance_residual_mm",
        "status",
        "detail",
    ],
    "uncertainty_summary": [
        "method",
        "variable",
        "n",
        "median",
        "p05",
        "p95",
        "evidence",
        "status",
    ],
    "regional_coverage": ["domain", "status", "detail"],
    "acceptance_results": ["stage", "check", "status", "reason"],
}


def synthetic_case():
    """Illustrative physical test input, never an observed field or fitted model."""
    first = date(2020, 6, 1)
    weather = []
    for i in range(120):
        weather.append(
            {
                "date": (first + timedelta(days=i)).isoformat(),
                "tmin_c": 18.0,
                "tmax_c": 32.0,
                "precipitation_mm": 12.0 if i % 12 == 0 else 0.0,
                "solar_radiation_mj_m2": 20.0,
                "relative_humidity_pct": 65.0,
                "wind_speed_m_s": 2.0,
            }
        )
    return {
        "crop": "maize",
        "start_date": weather[0]["date"],
        "end_date": weather[-1]["date"],
        "latitude_deg": 37.0,
        "elevation_m": 20.0,
        "et0_method": "hargreaves",
        "wind_height_m": 10.0,
        "weather": weather,
        "soil_layers": [
            {
                "thickness_mm": 300.0,
                "air_dry": 0.05,
                "wilting_point": 0.1,
                "field_capacity": 0.3,
                "saturation": 0.45,
                "ksat_mm_day": 40.0,
            }
            for _ in range(2)
        ],
        "initial_theta": [0.25, 0.25],
        "technology": {
            "method": "surface_drip",
            "wetted_fraction": 0.25,
            "application_evaporation_fraction": 0.0,
            "drift_fraction": 0.0,
            "canopy_fraction": 0.0,
            "application_depth_mm": 0.0,
        },
        "irrigation_events": [
            {
                "event_id": f"event-{i}",
                "date": weather[i]["date"],
                "amount_mm": 20.0,
                "measurement_location": "field",
            }
            for i in range(0, 100, 10)
        ],
    }


def numerical_cases():
    dry = synthetic_case()
    wet = deepcopy(dry)
    wet["initial_theta"] = [0.4, 0.4]
    wet["technology"]["wetted_fraction"] = 1.0
    wet["irrigation_events"] = []
    for weather in wet["weather"]:
        weather["precipitation_mm"] = 35.0
    return {"dry": dry, "wet": wet}


def legacy_season(inputs):
    """Unchanged legacy crop engine with shared forcing and fixed target placeholder."""
    from core.crop_model import daily_engine as engine
    from core.hydrology.evapotranspiration import wind_to_2m

    baseline = subprocess.run(
        ["git", "show", "560628c:core/crop_model/daily_engine.py"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if baseline != Path(engine.__file__).read_text():
        raise ValueError(
            "Legacy engine differs from frozen baseline; explicit baseline checkout required"
        )
    weather = []
    for row in inputs["weather"]:
        if (
            "relative_humidity_pct" not in row
            or "wind_speed_m_s" not in row
            or "wind_height_m" not in inputs
        ):
            raise ValueError(
                "Legacy comparison needs explicit humidity, wind and measurement height"
            )
        wind10 = wind_to_2m(
            row["wind_speed_m_s"], inputs["wind_height_m"]
        ) / wind_to_2m(1.0, 10.0)
        weather.append(
            {
                "DateTime": row["date"],
                "temperature_2m_min": row["tmin_c"],
                "temperature_2m_max": row["tmax_c"],
                "temperature_2m_mean": (row["tmin_c"] + row["tmax_c"]) / 2,
                "shortwave_radiation_sum": row["solar_radiation_mj_m2"],
                "precipitation_sum": row["precipitation_mm"],
                "relative_humidity_2m_mean": row["relative_humidity_pct"],
                "windspeed_10m_mean": wind10,
                "windspeed_10m_min": wind10,
                "windspeed_10m_max": wind10,
            }
        )
    layers = []
    for layer, theta in zip(inputs["soil_layers"], inputs["initial_theta"]):
        depth = layer["thickness_mm"]
        layers.append(
            {
                "thickness_mm": depth,
                "ll15_mm": layer["wilting_point"] * depth,
                "dul_mm": layer["field_capacity"] * depth,
                "sat_mm": layer["saturation"] * depth,
                "initial_water_mm": theta * depth,
                "ks_mm_day": layer["ksat_mm_day"],
                "kl": 0.06,
                "xf": 1.0,
            }
        )
    if any(e["measurement_location"] != "field" for e in inputs["irrigation_events"]):
        raise ValueError("Legacy comparison requires field-metered irrigation")
    irrigation = [
        {
            "date": e["date"],
            "amount_mm": e["amount_mm"],
            "method": inputs["technology"]["method"],
        }
        for e in inputs["irrigation_events"]
    ]
    payload = {
        "cropseason": {
            "crop": inputs["crop"],
            "planting_date": inputs["start_date"],
            "season_start": inputs["start_date"],
            "season_end": inputs["end_date"],
            "latitude": inputs["latitude_deg"],
            "longitude": inputs.get("longitude_deg", 0.0),
            "yield_target_kg_ha": 5000.0,
            "variety": {"maturation_group": inputs.get("maturity_group", "middle")},
        },
        "soil": {"layers": layers},
        "weather": {"daily": weather},
        "management": {
            "irrigation_method": inputs["technology"]["method"],
            "applied": {"irrigations": irrigation},
            "stresses": {"water": True, "nutrition": []},
        },
        "output": {
            "daily": [
                "date",
                "lai",
                "aboveground_biomass_kg_ha",
                "grain_yield_kg_ha",
                "aet_mm",
                "drainage_mm",
            ]
        },
    }
    result = engine._run_daily_engine(
        engine._normalize_simulation_payload(payload),
        decision_date=None,
        include_actions=False,
        filter_outputs=False,
    )
    daily = result["daily_state"]
    for row in daily:
        row["date"] = row["Date"]
        row["et_mm"] = row["actual_et_mm"]
        row["yield_kg_ha"] = row["grain_weight_kg_ha"]
        row["biomass_kg_ha"] = row["total_biomass_kg_ha"]
        row["soil_theta"] = [
            l["soil_water_mm"] / ((l["depth_bottom_cm"] - l["depth_top_cm"]) * 10)
            for l in row["layered_soil_water"]
        ]
        row["wet_theta"] = (
            row["soil_theta"] if inputs["technology"]["wetted_fraction"] == 1 else []
        )
        row["dry_theta"] = []
    summary = {
        "et_mm": sum(r["aet_mm"] for r in daily),
        "yield_kg_ha": daily[-1]["yield_kg_ha"],
        "bottom_drainage_mm": sum(r["drainage_mm"] for r in daily),
        "target_yield_source": "fixed legacy interface placeholder; not observed",
        "anthesis_date": next((r["date"] for r in daily if r["BBCH"] >= 61), None),
        "maturity_date": next((r["date"] for r in daily if r["BBCH"] >= 91), None),
    }
    return {"daily": daily, "summary": summary}


def _structures(inputs, parameters):
    structures = {}
    for name in ("single_domain", "dual_domain", "no_exchange", "static_evaporation"):
        x = deepcopy(inputs)
        p = deepcopy(parameters)
        p.setdefault("hydrology", {})
        if name == "single_domain":
            x["technology"]["wetted_fraction"] = 1.0
        if name == "no_exchange":
            p["hydrology"]["exchange_mm_day"] = 0.0
        if name == "static_evaporation":
            p["hydrology"]["dynamic_evaporation"] = False
        structures[name] = simulate_season(x, p)
    return structures


def _legacy_overflow_counterexample():
    # Execute the untouched baseline function without importing orchestration or
    # supplying an observed/target yield. Exposes its physical saturation loss.
    path = Path(__file__).parents[2] / "core/crop_model/daily_engine.py"
    tree = ast.parse(path.resolve().read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_add_infiltration"
    )
    namespace = {}
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            "baseline-water-function",
            "exec",
        ),
        namespace,
    )
    layer = {"water_mm": 25.0, "saturation_mm": 45.0}
    namespace["_add_infiltration"]([layer], precipitation=50.0, irrigation=0.0)
    return 50.0 - (layer["water_mm"] - 25.0)


def _case_pairs(case, result, observations):
    from .evaluation import pair_case

    return pair_case(case, result, observations)


def run_validation(config, output_directory):
    if config.get("frozen_parameters_path") and config.get("calibration_stages"):
        raise ValueError(
            "Imported frozen parameters and calibration stages are mutually exclusive"
        )
    out = Path(output_directory)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Run output exists; choose a new run-id to preserve evidence")
    out.mkdir(parents=True, exist_ok=True)
    seed = config.get("seed", 20261002)
    observations, split, qc, sources = audit_local_data(
        config["data_root"], out / "data_derived"
    )
    qc.to_csv(out / "qc_report.csv", index=False)
    tables = {name: [] for name in OUTPUT_COLUMNS}
    status = {}
    parameters = deepcopy(
        config.get(
            "parameters",
            {"version": "engineering-priors-v1", "hydrology": {"exchange_mm_day": 1.0}},
        )
    )
    cases = []
    groups = {}
    case_hashes = []
    for spec in config.get("cases", []):
        if (
            spec.get("management_complete") is not True
            or spec.get("plot_alignment_confirmed") is not True
        ):
            tables["acceptance_results"].append(
                dict(
                    stage="V1",
                    check=spec["case_id"],
                    status="insufficient_evidence",
                    reason="management or plot alignment unconfirmed",
                )
            )
            continue
        case = deepcopy(spec)
        if "inputs_path" in case:
            case["inputs"] = json.loads(Path(case["inputs_path"]).read_text())
            case_hashes.append(
                {"path": case["inputs_path"], "sha256": sha256(case["inputs_path"])}
            )
        case["observations"] = pd.read_csv(case["observations_path"])
        case_hashes.append(
            {
                "path": case["observations_path"],
                "sha256": sha256(case["observations_path"]),
            }
        )
        if case["split"] not in {"calibration", "validation", "excluded"}:
            raise ValueError("Explicit frozen split required")
        group = case["source_group_id"]
        if group in groups and groups[group] != case["split"]:
            raise ValueError(
                "Reused trial source appears in both calibration and validation"
            )
        groups[group] = case["split"]
        cases.append(case)
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    imported = None
    calibration_groups = []
    if config.get("frozen_parameters_path"):
        imported = load_frozen_parameters(
            config["frozen_parameters_path"], with_provenance=True
        )
        calibration_groups = imported["calibration_groups"]
        if set(calibration_groups) & {
            c["source_group_id"] for c in cases if c["split"] == "validation"
        }:
            raise ValueError(
                "Frozen calibration provenance overlaps validation (data leakage)"
            )
        parameters = imported["parameters"]
        case_hashes.append(
            {
                "path": config["frozen_parameters_path"],
                "sha256": sha256(config["frozen_parameters_path"]),
            }
        )
    from .evaluation import apply_registered_split, field_scores, evaluate_contrasts

    registered_split = apply_registered_split(cases)
    pd.DataFrame(registered_split).to_csv(out / "validation_split.csv", index=False)
    groups = {c["source_group_id"]: c["split"] for c in cases}
    # No calibration happens on the validation set. Each stage enumerates
    # explicit full parameter candidates and independent variable scales.
    training = [c for c in cases if c["split"] == "calibration"]
    if config.get("calibration_stages") and not training:
        tables["acceptance_results"].append(
            dict(
                stage="calibration",
                check="eligible_training",
                status="insufficient_evidence",
                reason="no verified calibration cases",
            )
        )
    elif config.get("calibration_stages"):
        for stage in config["calibration_stages"]:
            samples = pd.concat(
                [
                    c["observations"].assign(
                        split="calibration", source_group_id=c["source_group_id"]
                    )
                    for c in training
                ],
                ignore_index=True,
            )
            samples = samples[samples.variable.isin(stage["variables"])]
            check_observation_independence(samples.to_dict("records"))

            def objective(candidate, _):
                pairs = []
                for case in training:
                    pairs.extend(
                        _case_pairs(
                            case,
                            simulate_season(case["inputs"], candidate),
                            case["observations"],
                        )
                    )
                frame = pd.DataFrame(pairs)
                frame = frame[frame.variable.isin(stage["variables"])]
                return grouped_objective(frame, stage["scales"])

            parameters, report = calibrate_candidates(
                stage["parameter_candidates"], samples, objective
            )
            tables["parameter_identifiability"].append(
                dict(
                    parameter=stage["name"],
                    status="calibration_only_candidate_selection",
                    detail=json.dumps(report),
                )
            )
    if config.get("calibration_stages"):
        calibration_groups = sorted({c["source_group_id"] for c in training})
    frozen_hash = freeze_parameters(
        parameters, calibration_groups, out / "frozen_parameters.json"
    )
    matches = []
    structure_pairs = {}
    for case in cases:
        if case["split"] == "excluded":
            continue
        results = _structures(case["inputs"], parameters)
        primary = results["dual_domain"]
        matches.extend(_case_pairs(case, primary, case["observations"]))
        from types import SimpleNamespace

        try:
            baseline = legacy_season(case["inputs"])
            baseline_pairs = _case_pairs(
                case, SimpleNamespace(**baseline), case["observations"]
            )
            structure_pairs.setdefault("baseline_560628c", []).extend(baseline_pairs)
        except (ValueError, KeyError) as exc:
            tables["ablation_results"].append(
                dict(
                    case_id=case["case_id"],
                    structure="baseline_560628c",
                    evidence="verified_case",
                    status="insufficient_evidence",
                    detail=f"Baseline observation/input support unavailable: {exc}",
                )
            )
        for name, result in results.items():
            structure_pairs.setdefault(name, []).extend(
                _case_pairs(case, result, case["observations"])
            )
            tables["ablation_results"].append(
                dict(
                    case_id=case["case_id"],
                    structure=name,
                    evidence="verified_case",
                    **{
                        k: result.summary[k]
                        for k in (
                            "et_mm",
                            "yield_kg_ha",
                            "bottom_drainage_mm",
                            "balance_residual_mm",
                        )
                    },
                    status="observation_metrics_required",
                    detail="same frozen input and crop parameters; only declared structural switches",
                )
            )
            if name == "dual_domain":
                tables["water_balance"].extend(
                    dict(
                        case_id=case["case_id"],
                        structure=name,
                        evidence="verified_case",
                        **{
                            key: row[key] for key in OUTPUT_COLUMNS["water_balance"][3:]
                        },
                    )
                    for row in result.daily
                )
    tables["daily_predictions"].extend(matches)
    scores, status["V1"] = field_scores(cases, matches, seed)
    tables["metrics_by_group"].extend(scores)
    for structure, pairs in structure_pairs.items():
        metrics, _ = field_scores(cases, pairs, seed)
        tables["ablation_results"].extend(
            dict(
                structure=structure, evidence="verified_case_independent_metrics", **row
            )
            for row in metrics
        )
    contrast_rows, status["V2"] = evaluate_contrasts(
        config.get("contrasts", []), cases, matches
    )
    tables["treatment_contrasts"].extend(contrast_rows)
    from .validation_workflows import (
        evaluate_rotations,
        run_regional_workflow,
        observed_uncertainty,
        resolve_rotations,
    )

    rotation_rows, status["V3"] = evaluate_rotations(
        resolve_rotations(config.get("rotations", []), cases), parameters, seed
    )
    tables["ablation_results"].extend(rotation_rows)
    tables["uncertainty_summary"].extend(
        observed_uncertainty(
            config.get("observed_uncertainty", []), cases, parameters, seed
        )
    )
    status["V4"] = "insufficient_evidence"
    for stage, reason in [
        (
            "V1",
            "chronological independent complete-season, depth and phenology evidence",
        ),
        ("V2", "five independent significant comparable treatment contrasts"),
        (
            "V3",
            "continuous wheat-maize observations and declared initial-state sensitivity",
        ),
        ("V4", "independent regional coverage and validated method scope"),
    ]:
        tables["acceptance_results"].append(
            dict(
                stage=stage, check="evidence_gate", status=status[stage], reason=reason
            )
        )
    if config.get("software_benchmarks", True):
        x = synthetic_case()
        structures = _structures(x, parameters)
        for name, result in structures.items():
            tables["ablation_results"].append(
                dict(
                    case_id="synthetic-physics",
                    structure=name,
                    evidence="synthetic_software_test",
                    **{
                        k: result.summary[k]
                        for k in (
                            "et_mm",
                            "yield_kg_ha",
                            "bottom_drainage_mm",
                            "balance_residual_mm",
                        )
                    },
                    status="insufficient_field_evidence",
                    detail="engineering illustration; no field calibration or ranking evidence",
                )
            )
            tables["water_balance"].extend(
                dict(
                    case_id="synthetic-physics",
                    structure=name,
                    evidence="synthetic_software_test",
                    **{key: row[key] for key in OUTPUT_COLUMNS["water_balance"][3:]},
                )
                for row in result.daily
            )
        baseline = legacy_season(x)
        tables["ablation_results"].append(
            dict(
                case_id="synthetic-physics",
                structure="baseline_560628c",
                evidence="synthetic_software_test",
                **{
                    key: baseline["summary"][key]
                    for key in ("et_mm", "yield_kg_ha", "bottom_drainage_mm")
                },
                status="insufficient_field_evidence",
                detail="unchanged baseline crop engine; shared forcing; fixed non-observed target, nutrition disabled",
            )
        )
        old_loss = _legacy_overflow_counterexample()
        tables["ablation_results"].append(
            dict(
                case_id="50-mm-overflow",
                structure="baseline_560628c",
                evidence="synthetic_software_test",
                balance_residual_mm=old_loss,
                status="fail",
                detail="baseline surface function loses 30 mm; full field comparison requires verified cases",
            )
        )
        residual = max(
            abs(row["balance_residual_mm"]) for row in tables["water_balance"]
        )
        status["V0"] = "pass" if residual <= 1e-6 else "fail"
        tables["acceptance_results"].append(
            dict(
                stage="V0",
                check="daily_water_conservation",
                status=status["V0"],
                reason=f"max abs residual={residual:.12g} mm; synthetic engineering cases only",
            )
        )
        for numerical_case_id, numerical_input in numerical_cases().items():
            convergence = {}
            for n in (12, 24, 48):
                p = deepcopy(parameters)
                p.setdefault("hydrology", {})["substeps"] = n
                convergence[n] = simulate_season(numerical_input, p).summary
            for variable in ("et_mm", "bottom_drainage_mm", "yield_kg_ha"):
                delta = abs(
                    convergence[24][variable] - convergence[48][variable]
                ) / max(abs(convergence[48][variable]), 1e-8)
                tables["acceptance_results"].append(
                    dict(
                        stage="V0",
                        check="substep_convergence_"
                        + numerical_case_id
                        + "_"
                        + variable,
                        status="pass" if delta <= 0.02 else "fail",
                        reason=f"24 vs 48 relative difference={delta:.6g}; 12/24/48 raw values="
                        + json.dumps(
                            {str(n): convergence[n][variable] for n in convergence}
                        ),
                    )
                )
                if delta > 0.02:
                    status["V0"] = "fail"
        ranges = config.get(
            "uncertainty_ranges",
            {
                "shared": {"rue_g_mj": [2.5, 3.2], "exchange_mm_day": [0.0, 2.0]},
                "technology": {
                    "flood": {"wetted_fraction": [1.0, 1.0]},
                    "surface_drip": {"wetted_fraction": [0.2, 0.4]},
                    "micro_sprinkler": {"wetted_fraction": [0.4, 0.8]},
                    "sprinkler": {"wetted_fraction": [1.0, 1.0]},
                },
            },
        )
        draws = joint_samples(ranges, config.get("uncertainty_samples", 200), seed)
        values = {
            method: {key: [] for key in ("yield_kg_ha", "et_mm", "bottom_drainage_mm")}
            for method in ranges["technology"]
        }
        for draw in draws:
            for method, params in draw.items():
                inputs = deepcopy(x)
                inputs["technology"].update(
                    method=method, wetted_fraction=params["wetted_fraction"]
                )
                p = deepcopy(parameters)
                p.setdefault("crop", {}).setdefault("profile", {})["rue_g_mj"] = params[
                    "rue_g_mj"
                ]
                p.setdefault("hydrology", {})["exchange_mm_day"] = params[
                    "exchange_mm_day"
                ]
                result = simulate_season(inputs, p)
                for key in values[method]:
                    values[method][key].append(result.summary[key])
        for method, outputs in values.items():
            for variable, array in outputs.items():
                tables["uncertainty_summary"].append(
                    dict(
                        method=method,
                        variable=variable,
                        n=len(array),
                        **interval_summary(array),
                        evidence="synthetic_shared_prior_samples",
                        status="not_field_validated",
                    )
                )
    else:
        status["V0"] = "insufficient_evidence"
    # Upper evidence stages cannot bypass physical/numerical prerequisites.
    if status["V0"] != "pass":
        for stage in ["V1", "V2", "V3"]:
            if status[stage] == "pass":
                status[stage] = "insufficient_evidence"
    if status["V1"] != "pass":
        for stage in ["V2", "V3"]:
            if status[stage] == "pass":
                status[stage] = "insufficient_evidence"
    status["validated_methods"] = (
        sorted({r[key] for r in contrast_rows for key in ["method_a", "method_b"]})
        if status["V2"] == "pass"
        else []
    )
    regional_rows, status["V4"] = run_regional_workflow(
        config.get("regional"), parameters, status
    )
    tables["regional_coverage"].extend(regional_rows)
    for row in tables["acceptance_results"]:
        if row["check"] == "evidence_gate":
            row["status"] = status[row["stage"]]
    if not tables["parameter_identifiability"]:
        tables["parameter_identifiability"].append(
            dict(
                parameter="all",
                status="insufficient_evidence",
                detail="no verified calibration cases; engineering priors are not fitted estimates or a posterior",
            )
        )
    if not tables["regional_coverage"]:
        tables["regional_coverage"].append(
            dict(
                domain="North China Plain",
                status="insufficient_evidence",
                detail="canonical ERA5, soil, crop map, Sentinel/SIF observation operators and aquifer/background stores not yet aligned",
            )
        )
    for name, rows in tables.items():
        # Preserve extra diagnostic fields, but empty outputs still have schemas.
        columns = list(
            dict.fromkeys(OUTPUT_COLUMNS[name] + [key for row in rows for key in row])
        )
        pd.DataFrame(rows, columns=columns).to_csv(out / (name + ".csv"), index=False)
    version = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    manifest = {
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": version,
        "git_worktree_dirty": bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=Path(__file__).parent,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        ),
        "dependency_versions": {
            package: __import__("importlib.metadata", fromlist=["version"]).version(
                package
            )
            for package in ["numpy", "pandas", "pytest"]
        },
        "python": sys.version,
        "platform": platform.platform(),
        "seed": seed,
        "uncertainty_samples": config.get("uncertainty_samples", 200),
        "parameters_sha256": frozen_hash,
        "imported_frozen_sha256": imported["sha256"] if imported else None,
        "calibration_groups": calibration_groups,
        "data_sources": sources,
        "case_sources": case_hashes,
        "case_splits": groups,
        "raw_data_altered": False,
        "stage_status": status,
        "config": config,
        "software_results_are_field_validation": False,
        "model_limitations": [
            "fixed independent nutrition factor; no N cycle",
            "bucket water-table closure needs shallow-water validation",
            "raw unit and plot management confirmation incomplete",
        ],
    }
    (out / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str, allow_nan=False) + "\n"
    )
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if (
        not args.run_id
        or Path(args.run_id).name != args.run_id
        or args.run_id in {".", ".."}
    ):
        parser.error("run-id must be a single safe directory name")
    path = Path(args.config).resolve()
    config = json.loads(path.read_text())
    out = Path(__file__).with_name("results") / args.run_id
    status = run_validation(config, out)
    print(json.dumps({"output": str(out), "stage_status": status}, ensure_ascii=False))
    return 1 if "fail" in status.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
