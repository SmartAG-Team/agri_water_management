"""Match every field target to its unchanged archived observation."""
import numpy as np
import pandas as pd


def verify_field_targets(frame: pd.DataFrame, original: pd.DataFrame):
    if len(original) != 32 or not original.case_id.is_unique:
        raise ValueError('Expected 32 unique original field case identities')
    if frame.empty or frame.version.isna().any():
        raise ValueError('Field case identities are missing')
    source = original.set_index('case_id')
    for version, group in frame.groupby('version'):
        if len(group) != 32 or not group.case_id.is_unique or set(group.case_id) != set(source.index):
            raise ValueError('Field case identities differ from the archived source')
        expected = source.loc[group.case_id]
        for column in ['site', 'crop', 'harvest_year', 'treatment', 'source_group_id', 'et_eligible']:
            if not np.all(group[column].to_numpy() == expected[column].to_numpy()):
                raise ValueError('Field source observation identity changed: ' + column)
        for column in ['observed_et_mm', 'yield_13pct_kg_ha', 'observed_biomass_kg_ha']:
            if not np.allclose(group[column].to_numpy(float), expected[column].to_numpy(float),
                               rtol=0., atol=1e-8, equal_nan=False):
                raise ValueError('Field source observation changed: ' + column)
