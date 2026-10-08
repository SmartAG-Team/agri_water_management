"""Separate documented-field fitting loss from conditional station diagnostics."""
from qualified_metrics import losses as combined_losses


def losses(station,field):
    result=combined_losses(station,field)
    result['conditional_station_growth_diagnostic_loss']=result.station_growth_yield_loss
    result['station_growth_yield_loss']=0.
    result['calibration_data_loss']=result[['field_seasonal_ET_loss','field_grain_yield_loss',
        'field_biomass_loss','response_loss']].sum(axis=1)
    return result
