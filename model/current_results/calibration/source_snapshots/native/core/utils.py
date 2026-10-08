from crops.maize.phenology import MaizePhenology
import pandas as pd
import json,os

src_dir= os.path.dirname(os.path.abspath(__file__))
def get_maize_phenology_data(latitude, longitude, start_date, end_date, weather_data=None):
    """
    Compute maize phenology stages from caller-supplied daily weather data.
    
    Parameters:
        latitude (float): Latitude of the location.
        longitude (float): Longitude of the location.
        start_date (str): Start date in 'YYYY-MM-DD' format.
        end_date (str): End date in 'YYYY-MM-DD' format.

    weather_data:
        Daily weather rows from the request body.
    """
    if weather_data is None:
        raise ValueError("weather_data must be supplied from the request body")
    weather_data = pd.DataFrame(weather_data).copy()
    if weather_data.empty:
        raise ValueError("No weather data available for the specified dates.")
    phenology = MaizePhenology(params={"variety_maturation_group": 'early'})
    planting_date = start_date  # Assuming planting date is the start date

    return phenology.compute_stages(planting_date, weather=weather_data)
def favorability_to_category(favorability_value, cagetories):
    """tbd"""
    for k, v in cagetories.items():
        ke = [int(dd) for dd in k.split("-")]
        if ke[0] <= favorability_value * 100 < ke[1]:
            return v
    return None
def get_disease_insect_weed_status_and_code(category:str):
    stress_code={1: 'LOW', 2: 'MEDIUM', 3: 'HIGH', 4: 'PROTECTED'}
    code_stress={v:k for k,v in stress_code.items()}
    if category =='stress_code':
        return stress_code
    else:
        return code_stress
def get_water_status_and_code(category:str):
    stress_code={1: 'LOW', 2: 'MEDIUM', 3: 'HIGH', 4:  'IRRIGATED'}
    code_stress={v:k for k,v in stress_code.items()}
    if category =='stress_code':
        return stress_code
    else:
        return code_stress
def get_nutrition_status_and_code(category:str):
    stress_code={1: 'LOW', 2: 'MEDIUM', 3: 'HIGH', 4:  'FERTILIZED'}
    code_stress={v:k for k,v in stress_code.items()}
    if category =='stress_code':
        return stress_code
    else:
        return code_stress
