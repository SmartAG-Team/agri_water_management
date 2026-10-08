from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional, Any, List, Tuple
import pandas as pd
@dataclass
class InfectionResult:
    infected: bool
    infection_date: Optional[pd.Timestamp]
    # optional intermediate trace for debugging/QA
    intermediate: Optional[pd.DataFrame] = None
class Disease(ABC):
    """
    Abstract disease interface for infection simulation and spray timing advice.
    """

    def __init__(self, eppo_code: str):
        self.eppo_code = eppo_code

    # -------- primary & secondary infection APIs --------

    @abstractmethod
    def simulate_disease_daily_risk(self, weather: pd.DataFrame, growth_stage: pd.DataFrame,variety_sustainability) -> List[InfectionResult]:
        """
        Simulate daily infection risk based on weather, growth stage, and management.

        :return: A list of InfectionResult objects, one for each day in the simulation period.
        :rtype: List[InfectionResult]
        """
        pass

        
    