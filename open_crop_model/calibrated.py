"""Selected wheat and maize parameters for the daily crop–water model."""
from pathlib import Path
import json
from research.ncp_irrigation.model import SeasonResult, simulate_season

PARAMETERS = Path(__file__).resolve().parent / 'parameters'


def load_parameters(crop):
    if crop not in {'wheat', 'maize'}:
        raise ValueError('Calibrated crop must be wheat or maize')
    return json.loads((PARAMETERS / f'{crop}.json').read_text())


__all__ = ['SeasonResult', 'load_parameters', 'simulate_season']
