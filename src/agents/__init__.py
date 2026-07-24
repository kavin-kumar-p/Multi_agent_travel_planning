# Each agent is self-contained: business logic + A2A server in one file.
# Import the app objects for use by uvicorn via src.a2a.launch.
from src.agents.attractions import app as attractions_app
from src.agents.flight import app as flight_app
from src.agents.hotel import app as hotel_app
from src.agents.transport import app as transport_app

__all__ = ["flight_app", "attractions_app", "hotel_app", "transport_app"]
