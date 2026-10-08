from abc import ABC, abstractmethod
from .phenology import BasePhenology
from .spray_weather import BaseSprayWeather


class Crop(ABC):
    """
    Abstract base class for all crops.
    Shared logic for phenology, biomass, soil, and pest/disease models.
    """
    def __init__(self, params: dict):
        self.params = params
    @abstractmethod
    def simulate_growth(self, weather_data: list):
        """
        Crop-specific simulation logic.
        Should at minimum call phenology and biomass models.
        """
        pass
    @abstractmethod
    def calculate_gdd_from_temperature_mean_2m(self,temperature_mean_2m:float):
        pass
    @staticmethod
    def get_field_risk_for_a_day(status_nums,status_num):
        num_status = {v: k for k, v in status_num.items()}
        max_value = max(status_nums)
        if max_value == 4 and len(status_nums[status_nums < 4]) == 0:  # all protected
            return num_status[max_value]
        elif max_value == 4 and len(status_nums[status_nums < 4]) > 0:
            risk_not_under_pretection = max(status_nums[status_nums < 4])
            if risk_not_under_pretection > 1:
                return num_status[risk_not_under_pretection]
            else:
                return num_status[max_value]
        else:
            return num_status[max_value]