from core.crop import Crop
from core.soil import Soil
from core.management import Management
from core.field import Field
import datetime
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple


class CropSeason:
    """
    Central orchestrator for crop growth simulation integrating crop, soil, weather, and management.
    Simulates phenology, biomass, and stress factors from nutrition, water, disease, insects, and weeds.
    """
    
    def __init__(
        self, 
        start: datetime.date, 
        end: datetime.date, 
        crop: Crop = None, 
        soil: Soil = None, 
        weather_hourly: pd.DataFrame = None, 
        management: Management = None, 
        field: Field = None
    ):
        self.crop: Crop = crop
        self.soil: Soil = soil
        self.weather_hourly: pd.DataFrame = weather_hourly
        self.management: Management = management
        self.field: Field = field
        self.start: datetime.date = start
        self.end: datetime.date = end
        
        # Initialize simulation state variables
        self.daily_results: pd.DataFrame = None
        self.cumulative_gdd: float = 0.0
        self.current_growth_stage: str = "emergence"
        self.biomass: Dict[str, float] = {
            "leaf": 0.0,
            "stem": 0.0,
            "root": 0.0,
            "grain": 0.0,
            "total": 0.0
        }
        self.stress_factors: Dict[str, float] = {
            "water": 1.0,
            "nutrition": 1.0,
            "disease": 1.0,
            "insects": 1.0,
            "weeds": 1.0,
            "temperature": 1.0
        }

    def hourly_weather_to_daily(self) -> pd.DataFrame:
        """Convert hourly weather data to daily aggregated values."""
        if self.weather_hourly is None or self.weather_hourly.empty:
            raise ValueError("No hourly weather data available")
            
        weather_daily = self.weather_hourly.copy()
        weather_daily['DateTime'] = pd.to_datetime(weather_daily['DateTime'])
        weather_daily['Date'] = weather_daily['DateTime'].dt.date
        
        # Aggregate weather variables by day
        daily_agg = weather_daily.groupby('Date').agg({
            'temperature_2m': ['min', 'max', 'mean'],
            'relativehumidity_2m': 'mean',
            'precipitation': 'sum',
            'windspeed_10m': 'mean',
            'shortwave_radiation': 'sum',
            'soil_moisture_0_to_7cm': 'mean',
            'soil_moisture_7_to_28cm': 'mean',
            'soil_temperature_0_to_7cm': 'mean'
        }).round(2)
        
        # Flatten column names
        daily_agg.columns = ['_'.join(col).strip() for col in daily_agg.columns.values]
        daily_agg = daily_agg.reset_index()
        
        return daily_agg

    def calculate_stress_factors(self, daily_weather: pd.Series, day_index: int) -> Dict[str, float]:
        """Calculate daily stress factors affecting crop growth."""
        stress = self.stress_factors.copy()
        
        # Water stress (based on soil moisture and precipitation)
        if 'soil_moisture_0_to_7cm_mean' in daily_weather:
            soil_moisture = daily_weather['soil_moisture_0_to_7cm_mean']
            if soil_moisture < 0.2:  # Severe drought
                stress['water'] = 0.3
            elif soil_moisture < 0.3:  # Moderate drought
                stress['water'] = 0.6
            elif soil_moisture > 0.8:  # Waterlogged
                stress['water'] = 0.7
            else:
                stress['water'] = 1.0
        
        # Temperature stress
        if 'temperature_2m_mean' in daily_weather:
            temp_mean = daily_weather['temperature_2m_mean']
            if temp_mean < 10 or temp_mean > 35:  # Extreme temperatures
                stress['temperature'] = 0.5
            elif temp_mean < 15 or temp_mean > 30:  # Suboptimal temperatures
                stress['temperature'] = 0.8
            else:
                stress['temperature'] = 1.0
        
        # Disease stress (increases with humidity and moderate temperatures)
        if 'relativehumidity_2m_mean' in daily_weather and 'temperature_2m_mean' in daily_weather:
            humidity = daily_weather['relativehumidity_2m_mean']
            temp = daily_weather['temperature_2m_mean']
            if humidity > 80 and 20 < temp < 30:
                stress['disease'] = max(0.6, stress['disease'] - 0.05)  # Gradual disease buildup
            else:
                stress['disease'] = min(1.0, stress['disease'] + 0.02)  # Slow recovery
        
        # Nutrition stress (simplified - could be enhanced with soil nutrient modeling)
        # Assumes gradual nutrient depletion over season without fertilization
        if self.management and hasattr(self.management, 'fertilization_schedule'):
            # Check if fertilization occurred recently
            recent_fertilization = False  # Placeholder for fertilization logic
            if not recent_fertilization:
                stress['nutrition'] = max(0.7, 1.0 - (day_index / 150) * 0.3)
        
        # Weed and insect stress (simplified models)
        stress['weeds'] = max(0.8, 1.0 - (day_index / 100) * 0.2)  # Gradual weed competition
        stress['insects'] = 0.95  # Baseline insect pressure
        
        return stress

    def calculate_daily_biomass_increment(
        self, 
        daily_gdd: float, 
        stress_factors: Dict[str, float],
        growth_stage: str
    ) -> Dict[str, float]:
        """Calculate daily biomass accumulation for different plant parts."""
        # Base biomass increment based on GDD and growth stage
        base_increment = daily_gdd * 0.1  # Base relationship between GDD and biomass
        
        # Overall stress factor (multiplicative effect)
        overall_stress = np.prod(list(stress_factors.values()))
        
        # Growth stage-specific partitioning
        partitioning = {
            "emergence": {"leaf": 0.6, "stem": 0.2, "root": 0.2, "grain": 0.0},
            "vegetative": {"leaf": 0.5, "stem": 0.3, "root": 0.2, "grain": 0.0},
            "reproductive": {"leaf": 0.2, "stem": 0.2, "root": 0.1, "grain": 0.5},
            "maturity": {"leaf": 0.0, "stem": 0.0, "root": 0.0, "grain": 1.0}
        }
        
        stage_partitioning = partitioning.get(growth_stage, partitioning["vegetative"])
        
        # Calculate increments for each plant part
        increments = {}
        for part, fraction in stage_partitioning.items():
            increments[part] = base_increment * fraction * overall_stress
        
        increments["total"] = sum(increments.values())
        
        return increments

    def update_growth_stage(self, cumulative_gdd: float) -> str:
        """Update growth stage based on cumulative GDD."""
        # Simplified growth stage thresholds (could be crop-specific)
        if cumulative_gdd < 200:
            return "emergence"
        elif cumulative_gdd < 800:
            return "vegetative"
        elif cumulative_gdd < 1500:
            return "reproductive"
        else:
            return "maturity"

    def simulate_season(self) -> pd.DataFrame:
        """
        Run complete season simulation integrating all components.
        Returns daily results with phenology, biomass, and stress factors.
        """
        # Convert hourly weather to daily
        daily_weather = self.hourly_weather_to_daily()
        
        # Initialize results dataframe
        date_range = pd.date_range(start=self.start, end=self.end, freq='D')
        results = []
        
        # Reset simulation state
        self.cumulative_gdd = 0.0
        self.current_growth_stage = "emergence"
        self.biomass = {"leaf": 0.0, "stem": 0.0, "root": 0.0, "grain": 0.0, "total": 0.0}
        
        for day_index, current_date in enumerate(date_range):
            # Get weather for current day
            daily_weather_row = daily_weather[daily_weather['Date'] == current_date.date()]
            
            if daily_weather_row.empty:
                raise ValueError(f"Missing daily weather for {current_date.date()}")
                
            weather_data = daily_weather_row.iloc[0]
            
            # Calculate daily GDD using crop-specific method
            if hasattr(self.crop, 'calculate_gdd_from_temperature_mean_2m'):
                if 'temperature_2m_mean' not in weather_data or pd.isna(weather_data['temperature_2m_mean']):
                    raise ValueError(f"daily weather[{current_date.date()}] missing temperature_2m_mean")
                temp_mean = weather_data['temperature_2m_mean']
                daily_gdd = self.crop.calculate_gdd_from_temperature_mean_2m(temp_mean)
            else:
                # Fallback GDD calculation
                if 'temperature_2m_mean' not in weather_data or pd.isna(weather_data['temperature_2m_mean']):
                    raise ValueError(f"daily weather[{current_date.date()}] missing temperature_2m_mean")
                temp_mean = weather_data['temperature_2m_mean']
                daily_gdd = max(0, temp_mean - 10)  # Base temp 10°C
            
            self.cumulative_gdd += daily_gdd
            
            # Update growth stage
            self.current_growth_stage = self.update_growth_stage(self.cumulative_gdd)
            
            # Calculate stress factors
            self.stress_factors = self.calculate_stress_factors(weather_data, day_index)
            
            # Calculate biomass increment
            biomass_increment = self.calculate_daily_biomass_increment(
                daily_gdd, self.stress_factors, self.current_growth_stage
            )
            
            # Update cumulative biomass
            for part in self.biomass:
                self.biomass[part] += biomass_increment[part]
            
            # Store daily results
            result_row = {
                'date': current_date.date(),
                'day_of_season': day_index + 1,
                'daily_gdd': daily_gdd,
                'cumulative_gdd': self.cumulative_gdd,
                'growth_stage': self.current_growth_stage,
                'temperature_mean': weather_data.get('temperature_2m_mean', np.nan),
                'precipitation': weather_data.get('precipitation_sum', np.nan),
                'soil_moisture': weather_data.get('soil_moisture_0_to_7cm_mean', np.nan),
                **{f'biomass_{part}': self.biomass[part] for part in self.biomass},
                **{f'stress_{factor}': self.stress_factors[factor] for factor in self.stress_factors}
            }
            
            results.append(result_row)
        
        self.daily_results = pd.DataFrame(results)
        return self.daily_results

    def get_seasonal_summary(self) -> Dict:
        """Get summary statistics for the growing season."""
        if self.daily_results is None:
            raise ValueError("No simulation results available. Run simulate_season() first.")
        
        return {
            'total_gdd': self.cumulative_gdd,
            'final_growth_stage': self.current_growth_stage,
            'total_biomass': self.biomass['total'],
            'grain_yield': self.biomass['grain'],
            'average_stress_factors': {
                factor: self.daily_results[f'stress_{factor}'].mean() 
                for factor in self.stress_factors
            },
            'season_length_days': len(self.daily_results),
            'total_precipitation': self.daily_results['precipitation'].sum() if 'precipitation' in self.daily_results else 0
        }

    def get_critical_periods(self) -> Dict:
        """Identify critical periods during the growing season."""
        if self.daily_results is None:
            raise ValueError("No simulation results available. Run simulate_season() first.")
        
        critical_periods = {}
        
        # Water stress periods
        water_stress = self.daily_results['stress_water'] < 0.7
        if water_stress.any():
            critical_periods['water_stress_days'] = water_stress.sum()
            critical_periods['severe_water_stress_periods'] = self._identify_consecutive_periods(water_stress)
        
        # Disease pressure periods
        disease_stress = self.daily_results['stress_disease'] < 0.8
        if disease_stress.any():
            critical_periods['disease_pressure_days'] = disease_stress.sum()
        
        # Temperature stress periods
        temp_stress = self.daily_results['stress_temperature'] < 0.8
        if temp_stress.any():
            critical_periods['temperature_stress_days'] = temp_stress.sum()
        
        return critical_periods

    def _identify_consecutive_periods(self, stress_mask: pd.Series) -> List[Tuple[int, int]]:
        """Identify consecutive periods of stress."""
        periods = []
        start = None
        
        for i, is_stress in enumerate(stress_mask):
            if is_stress and start is None:
                start = i
            elif not is_stress and start is not None:
                periods.append((start, i - 1))
                start = None
        
        # Handle case where stress period extends to end of season
        if start is not None:
            periods.append((start, len(stress_mask) - 1))
        
        return periods
