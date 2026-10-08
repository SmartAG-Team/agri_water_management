class OpenMeteoWeatherService:
    """Disabled external weather service.

    Weather must be provided by API callers in request bodies.
    """

    def __init__(self, *_, **__):
        raise RuntimeError("weather must be supplied from the request body")
