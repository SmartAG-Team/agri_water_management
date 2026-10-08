# Crop and irrigation processes

The daily crop–water engine is `model.py`. Its input checks are in `contracts.py`; development, canopy growth, grain formation, irrigation policy and layered hydrology are separate process modules.

The calibrated entry points and effective wheat and maize parameter cards are described in the [package README](../../README.md). Soil profiles, weather and management remain explicit inputs. Crop state and soil-water storage can carry between successive simulation segments.
