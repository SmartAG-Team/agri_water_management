class Weather:
    """Disabled external weather adapter.

    Model requests must include weather in the request body. This class remains
    only so older scripts fail with a clear error instead of fetching or
    filling weather outside the request contract.
    """

    @staticmethod
    def _request_body_required():
        raise RuntimeError("weather must be supplied from the request body")

    @staticmethod
    def get_daily_temperature(latitude, longitude, start_date, end_date):
        Weather._request_body_required()

    @staticmethod
    def get_era5_weather_by_lat_lon(latitude, longitude, start_date, end_date):
        Weather._request_body_required()

    @staticmethod
    def get_hourly_for_lat_long(lat, lon, start_datetime, end_datetime):
        Weather._request_body_required()

    @staticmethod
    def get_hourly_for_lat_long_formated(lat, lon, start_datetime, end_datetime):
        Weather._request_body_required()
