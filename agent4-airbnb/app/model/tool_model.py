
from pydantic import BaseModel, Field

class GetPlaceIdInput(BaseModel):
    location: str = Field(
        ...,
        description="The name of the location to resolve to a Google Maps Place ID. "
                    "Can be a city, region, country, or specific area. "
                    "Examples: 'Bali, Indonesia', 'Tokyo, Japan', 'New York, USA'"
    )