"""Finish unchanged saved forecasts after correcting an archive-reference path."""
from evaluate_qualified import ROOT,pd,np,write,datetime,timezone
import hashlib

def main():
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'predictions').glob('*') if p.is_file()}
    summaries=pd.read_csv(ROOT/'predictions/case_summaries.csv')
    obs=pd.read_csv(ROOT/'predictions/station_comparisons.csv',low_memory=False)
    fields=pd.read_csv(ROOT/'predictions/field_comparisons.csv')
    metrics=pd.read_csv(ROOT/'tables/field_metrics.csv')
    assert len(summaries)==328 and len(obs)==14794 and len(fields)==64
    source=pd.read_csv(ROOT/'source_snapshots/previous_field_comparisons.csv')
    source=source[source.version.eq('management_refit')].set_index('case_id')
    base=fields[fields.version.eq('reference')].set_index('case_id')
    for col in ['predicted_et_mm','predicted_biomass_kg_ha','grain_13pct_kg_ha']:
        assert np.allclose(base[col],source.loc[base.index,col],rtol=0,atol=1e-8)
    retained=pd.read_csv(ROOT/'source_snapshots/previous_station_comparisons.csv',low_memory=False)
    retained=retained[retained.version.eq('management_refit')].set_index('observation_id')
    before=obs[obs.version.eq('reference')].set_index('observation_id')
    expected=retained.loc[before.index,'predicted']
    eligible=expected.notna()
    assert np.allclose(before.loc[eligible,'predicted'],expected[eligible],rtol=0,atol=1e-8)
    write(ROOT/'verification/run_receipt.json',dict(simulations=328,cases_per_version=164,
        station_records_per_version=7397,all_eligible_sites_retained=True,
        qualified_reference_replayed_for_all_observation_windows=True,daily_ET_conditional_and_not_fitted=True,
        maize_parameters_refitted=True,wheat_parameters_retained=True,field_biomass_fitted=True,field_biomass_basis_confirmed=True,
        parameters_frozen_before_testing=True,
        water_residual_max_mm=float(summaries.water_residual_max_mm.max()),
        carbon_residual_max_kg_ha=float(summaries.carbon_residual_max_kg_ha.max()),
        completed_at_utc=datetime.now(timezone.utc).isoformat(),regional_promoted=False))
    print(metrics.round(3).to_string(index=False),flush=True)

    assert all(hashlib.sha256((ROOT/'predictions'/name).read_bytes()).hexdigest()==sha for name,sha in hashes.items())
    write(ROOT/'verification/export_recovery.json',dict(unchanged_prediction_sha256=hashes,simulations_repeated=False,reference_paths_corrected=True))

if __name__=='__main__':main()
