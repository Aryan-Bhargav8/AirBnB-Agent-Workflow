from langchain_core.tools import tool

import requests
from app.config import settings
from app.model.tool_model import GetPlaceIdInput

# url config
BASE_URL = "https://places.googleapis.com/v1"
HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key":settings.places_api_key.get_secret_value(),
    "X-Goog-FieldMask": "places.id,places.displayName"
}

#TODO: places api arg schema
#places api tool
# @tool(args_schema=)
@tool(args_schema=GetPlaceIdInput)
async def get_places_id(location:str) -> str:
    """Resolves a location name to a Google Maps Place ID for use in Airbnb search."""
    url =f"{BASE_URL}/places:searchText"
    headers = HEADERS
    res = requests.post(url, headers=headers, json={"textQuery": location})
    if res.status_code == 200:
        return res.json()
    else:
        return f"Error: {res.status_code} {res.text}"

