"""A chronological field partition that includes a dry calibration year."""
from math import isfinite
from numbers import Real

import pandas as pd


def field_split(year: int) -> str:
    if isinstance(year, bool) or not isinstance(year, Real) or not isfinite(year):
        raise ValueError('A numeric field harvest year is required')
    if int(year) != year or int(year) not in (2016, 2017, 2018, 2019):
        raise ValueError('Unknown or ambiguous field harvest year')
    return 'validation' if int(year) == 2019 else 'calibration'


def remap_field_split(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    result['split'] = result.harvest_year.map(field_split)
    expected = result.harvest_year.map(lambda year: f'Wuqiao-{int(year)}')
    if not result.source_group_id.eq(expected).all():
        raise ValueError('Field source group does not match its harvest year')
    if not result.groupby('source_group_id').split.nunique().eq(1).all():
        raise ValueError('A field source group spans calibration and testing')
    return result
