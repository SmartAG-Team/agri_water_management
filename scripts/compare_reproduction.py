"""Compare fresh regional recalculation with the published frozen outputs."""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
TABLES={
 'regional_policy_annual_results.csv':['policy','reduction_fraction','period','harvest_year'],
 'policy_comparison.csv':['scope','policy'],
 'contrast_summary.csv':['parameter_set','policy','reduction_fraction'],
 'training_selected_quotas.csv':['relative_class','grain_retention_target_pct'],
 'full_quota_spatial_metrics.csv':['crop','fraction','variable'],
}


def compare(fresh,reference):
    records=[]
    for filename,keys in TABLES.items():
        expected=pd.read_csv(reference/'tables'/filename)
        actual=pd.read_csv(fresh/'tables'/filename)
        assert list(actual.columns)==list(expected.columns),(filename,'columns')
        assert len(actual)==len(expected),(filename,'rows')
        sort=[k for k in keys if k in expected]
        assert sort and not expected.duplicated(sort).any(),(filename,'comparison keys')
        actual=actual.sort_values(sort).reset_index(drop=True)
        expected=expected.sort_values(sort).reset_index(drop=True)
        maximum={}
        for column in expected:
            if pd.api.types.is_numeric_dtype(expected[column]):
                a=actual[column].to_numpy(float);b=expected[column].to_numpy(float)
                assert np.allclose(a,b,rtol=1e-10,atol=1e-6,equal_nan=True),(filename,column)
                valid=np.isfinite(a)&np.isfinite(b)
                maximum[column]=float(np.max(np.abs(a[valid]-b[valid]))) if valid.any() else 0.
            else:
                assert actual[column].fillna('').astype(str).tolist()==expected[column].fillna('').astype(str).tolist(),(filename,column)
        records.append({'table':filename,'rows':len(actual),'all_columns_match':True,
                        'maximum_absolute_numeric_differences':maximum})
    receipt={'all_comparisons_passed':True,'fresh_predictions_compared_with_published_results':True,
             'numeric_rtol':1e-10,'numeric_atol':1e-6,'tables':records}
    (fresh/'verification/published_results_comparison.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fresh',type=Path,required=True)
    parser.add_argument('--reference',type=Path,default=ROOT/'model/current_results/regional')
    args=parser.parse_args()
    receipt=compare(args.fresh.resolve(),args.reference.resolve())
    print('Fresh regional outputs match all published columns in',len(receipt['tables']),'tables.')


if __name__=='__main__':main()
