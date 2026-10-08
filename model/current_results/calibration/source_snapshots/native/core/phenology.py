from datetime import datetime, timedelta

class BasePhenology:
    """
    Base class for crop phenology based on accumulated thermal time (GDD).
    Subclasses can override `stage_thresholds` or calculation methods.
    """
    def __init__(self, params: dict):
        self.base_temp = params.get("base_temperature", 8.0)  # default base temp

    def compute_daily_gdd(self, tmean: float = None, tmin: float = None, tmax: float = None, **kwargs) -> float:
        """
        Calculate Growing Degree Days (GDD) for a single day.
        """
        if tmean is None and "temperature_mean_2m" in kwargs:
            tmean = kwargs["temperature_mean_2m"]
        
        if tmean is not None:
            return max(0.0, tmean - self.base_temp)
        elif tmin is not None and tmax is not None:
            return max(0.0, ((tmax + tmin) / 2) - self.base_temp)
        else:
            raise ValueError("Either tmean or both tmin and tmax must be provided")
    def compute_stages(self, planting_date: str, weather: list = None):
        """
        Compute phenological stages based on daily GDD accumulation.
        
        Parameters:
            planting_date (str): 'YYYY-MM-DD'
            weather (list): list of dicts with 'tmin', 'tmax', 'date'

        Returns:
            List of tuples: [(stage, reached_date), ...]
        """
        if weather is None:
            raise ValueError("Weather data is required to compute phenology stages")

        accumulated_gdd = 0.0
        reached_stages = []
        thresholds = self.stage_thresholds.copy()
        planting_date_obj = datetime.strptime(planting_date, "%Y-%m-%d")

        for day in weather:
            if "temperature_2m_mean" in weather.columns:
                gdd=self.compute_daily_gdd(tmean=day["temperature_2m_mean"])
            else:    
                gdd=self.compute_daily_gdd(tmin=day["temperature_2m_min"],tmax=day["temperature_2m_max"])
            accumulated_gdd += gdd

            for stage, threshold in list(thresholds.items()):
                if accumulated_gdd >= threshold:
                    reached_stages.append((stage, day["date"]))
                    del thresholds[stage]

            if not thresholds:
                break  # all stages reached

        return reached_stages