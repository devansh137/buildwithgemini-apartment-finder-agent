# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

load_dotenv()

# Hardcoded project ID and Cloud Storage bucket name as required
PROJECT_ID = "qwiklabs-gcp-02-b67103ed1a1b"
BUCKET_NAME = "apartment-finder-media-qwiklabs-gcp-02-b67103ed1a1b"
MEMORY_BANK_ID = "2256996105638117376"

# Optional API key read from environment variable
ZIP_API_KEY = os.getenv("ZIP_API_KEY", "")

# Load Agent Engine resource name from deployment_metadata.json for code execution sandbox
DEPLOYMENT_METADATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json")
agent_engine_resource_name = f"projects/{PROJECT_ID}/locations/us-east1/reasoningEngines/{MEMORY_BANK_ID}"

if os.path.exists(DEPLOYMENT_METADATA_PATH):
    try:
        with open(DEPLOYMENT_METADATA_PATH, "r") as f:
            metadata = json.load(f)
            if "remote_agent_runtime_id" in metadata:
                agent_engine_resource_name = metadata["remote_agent_runtime_id"]
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=agent_engine_resource_name
)


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback to extract and write session events into Vertex AI Memory Bank after each turn."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory extraction skipped: {e}")
    return None



def memory_bank_service_builder():
    """Builds a VertexAiMemoryBankService pointing to the deployed Agent Engine Memory Bank."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


def get_maps_api_key() -> str:
    """Retrieve Google Maps API key from environment variables."""
    return os.getenv("GOOGLE_MAPS_API_KEY", "")


def get_firestore_client() -> firestore.Client:
    """Returns a Firestore client initialized with the hardcoded project ID."""
    return firestore.Client(project=PROJECT_ID, database="(default)")


def search_apartments(
    neighborhood: str = "",
    max_rent: int = 0,
    min_bedrooms: int = -1,
    pet_friendly_only: bool = False,
) -> str:
    """Search available apartment listings in Firestore with optional filters.

    Args:
        neighborhood: Filter by neighborhood name (e.g., 'Downtown', 'Arts District').
        max_rent: Maximum monthly rent limit (e.g., 2500). Set to 0 to disable.
        min_bedrooms: Minimum number of bedrooms required (e.g., 1 or 2). Set to -1 to disable.
        pet_friendly_only: If True, only return pet-friendly apartments.

    Returns:
        A list of matching apartment summaries from Firestore.
    """
    db = get_firestore_client()
    docs = db.collection("apartments").stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        if not data.get("available", True):
            continue

        if neighborhood and neighborhood.lower() not in data.get("neighborhood", "").lower():
            continue

        if max_rent > 0 and data.get("monthly_rent", 0) > max_rent:
            continue

        if min_bedrooms >= 0 and data.get("bedrooms", 0) < min_bedrooms:
            continue

        if pet_friendly_only and not data.get("pet_friendly", False):
            continue

        results.append(
            f"• [{data.get('id', doc.id)}] {data.get('title')}: {data.get('neighborhood')} | "
            f"{data.get('bedrooms')} Bed, {data.get('bathrooms')} Bath | "
            f"${data.get('monthly_rent')}/mo | Pet Friendly: {'Yes' if data.get('pet_friendly') else 'No'}"
        )

    if not results:
        return "No apartments found matching your specified criteria."

    return "Found the following matching apartments:\n" + "\n".join(results)


def get_apartment_details(apartment_id: str) -> str:
    """Fetch complete details for a specific apartment listing from Firestore.

    Args:
        apartment_id: The ID of the apartment listing (e.g., 'apt-101').

    Returns:
        Detailed information about the specified apartment.
    """
    db = get_firestore_client()
    doc_ref = db.collection("apartments").document(apartment_id)
    doc = doc_ref.get()

    if not doc.exists:
        return f"Apartment listing with ID '{apartment_id}' was not found in Firestore."

    data = doc.to_dict()
    amenities = ", ".join(data.get("amenities", [])) if data.get("amenities") else "None listed"

    return (
        f"Apartment ID: {data.get('id', doc.id)}\n"
        f"Title: {data.get('title')}\n"
        f"Neighborhood: {data.get('neighborhood')}\n"
        f"Rent: ${data.get('monthly_rent')}/month\n"
        f"Layout: {data.get('bedrooms')} Bedroom(s), {data.get('bathrooms')} Bathroom(s)\n"
        f"Pet Friendly: {'Yes' if data.get('pet_friendly') else 'No'}\n"
        f"Amenities: {amenities}\n"
        f"Description: {data.get('description')}\n"
        f"Available: {'Yes' if data.get('available') else 'No'}"
    )


def add_apartment_listing(
    title: str,
    neighborhood: str,
    bedrooms: int,
    bathrooms: float,
    monthly_rent: int,
    pet_friendly: bool = True,
    amenities: str = "",
    description: str = "",
) -> str:
    """Add a new apartment listing to the Firestore database.

    Args:
        title: The title of the apartment (e.g. 'Cozy 1BR in Midtown').
        neighborhood: The neighborhood name (e.g. 'Midtown').
        bedrooms: Number of bedrooms.
        bathrooms: Number of bathrooms.
        monthly_rent: Monthly rent price in USD.
        pet_friendly: Whether pets are allowed.
        amenities: Comma-separated list of amenities (e.g. 'Gym, Parking, Laundry').
        description: Brief description of the unit.

    Returns:
        Confirmation message with the created apartment ID.
    """
    db = get_firestore_client()

    timestamp_id = int(datetime.datetime.now().timestamp())
    apt_id = f"apt-{timestamp_id}"

    amenities_list = [a.strip() for a in amenities.split(",") if a.strip()] if amenities else []

    new_apartment = {
        "id": apt_id,
        "title": title,
        "neighborhood": neighborhood,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "monthly_rent": monthly_rent,
        "pet_friendly": pet_friendly,
        "amenities": amenities_list,
        "description": description,
        "available": True,
    }

    db.collection("apartments").document(apt_id).set(new_apartment)
    return f"Successfully added apartment listing '{title}' with ID '{apt_id}' to Firestore!"


def schedule_tour_request(
    apartment_id: str,
    preferred_date: str,
    user_name: str,
    user_email: str,
    tour_type: str = "in-person",
) -> str:
    """Schedule an apartment viewing tour and store the request in Firestore.

    Args:
        apartment_id: The ID of the apartment listing to tour (e.g., 'apt-101').
        preferred_date: Preferred date and time for the tour (e.g., '2026-10-05 14:00').
        user_name: Name of the person requesting the tour.
        user_email: Contact email address of the requestor.
        tour_type: Either 'in-person' or 'virtual'.

    Returns:
        Confirmation message with tour booking reference ID.
    """
    db = get_firestore_client()
    doc_ref = db.collection("apartments").document(apartment_id)
    doc = doc_ref.get()

    if not doc.exists:
        return f"Cannot schedule tour: Apartment ID '{apartment_id}' was not found in Firestore."

    apt_title = doc.to_dict().get("title", apartment_id)

    timestamp_id = int(datetime.datetime.now().timestamp())
    tour_id = f"tour-{timestamp_id}"

    tour_data = {
        "tour_id": tour_id,
        "apartment_id": apartment_id,
        "apartment_title": apt_title,
        "preferred_date": preferred_date,
        "user_name": user_name,
        "user_email": user_email,
        "tour_type": tour_type,
        "status": "confirmed",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    db.collection("tour_requests").document(tour_id).set(tour_data)

    return (
        f"Tour request submitted successfully!\n"
        f"Reference ID: {tour_id}\n"
        f"Property: {apt_title} ({apartment_id})\n"
        f"Requested Date/Time: {preferred_date}\n"
        f"Format: {tour_type}\n"
        f"Confirmation sent to: {user_name} ({user_email})"
    )


def lookup_zipcode_location(zipcode: str) -> str:
    """Fetch city, state, and geographic coordinates for a US ZIP code using the Zippopotam.us public API.

    Args:
        zipcode: 5-digit US ZIP code (e.g. '10001', '90210').

    Returns:
        Location details including place name, state, latitude, and longitude.
    """
    clean_zip = zipcode.strip()[:5]
    if not clean_zip.isdigit() or len(clean_zip) != 5:
        return f"Invalid ZIP code format: '{zipcode}'. Please provide a 5-digit US ZIP code."

    url = f"https://api.zippopotam.us/us/{clean_zip}"
    headers = {"User-Agent": "ApartmentFinderAgent/1.0"}

    if ZIP_API_KEY:
        headers["Authorization"] = f"Bearer {ZIP_API_KEY}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                return f"Could not retrieve location for ZIP code '{clean_zip}'."

            data = json.loads(response.read().decode("utf-8"))
            places = data.get("places", [])
            if not places:
                return f"No location records found for ZIP code '{clean_zip}'."

            place = places[0]
            city = place.get("place name", "Unknown City")
            state = place.get("state", "Unknown State")
            state_abbr = place.get("state abbreviation", "")
            lat = place.get("latitude", "N/A")
            lng = place.get("longitude", "N/A")

            return (
                f"Location for ZIP Code {clean_zip}:\n"
                f"• City/Area: {city}\n"
                f"• State: {state} ({state_abbr})\n"
                f"• Coordinates: Lat {lat}, Lng {lng}"
            )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"ZIP code '{clean_zip}' was not found."
        return f"HTTP error querying ZIP code API: {e.code}"
    except Exception as e:
        return f"Error fetching location for ZIP code '{clean_zip}': {str(e)}"


def geocode_address(address: str) -> str:
    """Convert a street address or location name into geographic coordinates using Google Maps Geocoding API.

    Args:
        address: Full street address or place name (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA' or 'Times Square, NY').

    Returns:
        Formatted location details including canonical address, latitude, and longitude.
    """
    api_key = get_maps_api_key()
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not configured."

    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            status = data.get("status")

            if status != "OK" or not data.get("results"):
                return f"Geocoding failed for address '{address}'. Status: {status}"

            result = data["results"][0]
            formatted_address = result.get("formatted_address", address)
            location = result.get("geometry", {}).get("location", {})
            lat = location.get("lat")
            lng = location.get("lng")

            return (
                f"Geocoding Result for '{address}':\n"
                f"• Address: {formatted_address}\n"
                f"• Coordinates: Latitude {lat}, Longitude {lng}"
            )
    except Exception as e:
        return f"Error connecting to Geocoding API: {str(e)}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
) -> str:
    """Find nearby points of interest (restaurants, cafes, parks, gyms, etc.) using Google Places API (New).

    Args:
        latitude: Latitude coordinate of the central location.
        longitude: Longitude coordinate of the central location.
        place_type: Type of place to search for (e.g., 'restaurant', 'cafe', 'park', 'gym', 'supermarket', 'subway_station').
        radius_meters: Search radius in meters (default 1000m / 1km).

    Returns:
        List of nearby places with name, formatted address, and coordinates.
    """
    api_key = get_maps_api_key()
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not configured."

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }

    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": float(radius_meters),
            }
        },
    }

    try:
        json_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=json_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            places = data.get("places", [])

            if not places:
                return f"No nearby '{place_type}' places found within {radius_meters}m of ({latitude}, {longitude})."

            results = []
            for p in places:
                name = p.get("displayName", {}).get("text", "Unknown Name")
                addr = p.get("formattedAddress", "No address")
                loc = p.get("location", {})
                lat = loc.get("latitude", "N/A")
                lng = loc.get("longitude", "N/A")
                results.append(f"• {name}\n  Address: {addr}\n  Location: ({lat}, {lng})")

            return f"Found {len(places)} nearby '{place_type}' places:\n" + "\n\n".join(results)
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8") if e.fp else str(e)
        return f"HTTP error calling Places API (New) ({e.code}): {error_msg}"
    except Exception as e:
        return f"Error calling Places API (New): {str(e)}"


def generate_apartment_image(
    prompt_description: str,
    tool_context: ToolContext,
) -> str:
    """Generate a visual image of an apartment unit or layout using gemini-3.1-flash-lite-image in the global region.

    Saves the image to Playground Artifacts and uploads it to public Cloud Storage, returning the public HTTPS URL.

    Args:
        prompt_description: Visual description of the apartment interior, exterior, or layout (e.g., 'Modern sunlit 1-bedroom loft with wood floors and city skyline views').
        tool_context: ToolContext provided by ADK.

    Returns:
        Public HTTPS URL of the generated image in Cloud Storage (https://storage.googleapis.com/<bucket>/<object>).
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=f"High quality interior photography of an apartment: {prompt_description}",
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )

    image_bytes = None
    mime_type = "image/jpeg"
    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                image_bytes = part.inline_data.data
                if part.inline_data.mime_type:
                    mime_type = part.inline_data.mime_type
                break

    if not image_bytes:
        return "Failed to generate image bytes from model response."

    timestamp_id = int(datetime.datetime.now().timestamp())
    filename = f"apartment_visual_{timestamp_id}.jpg"

    # 1. Save artifact to Playground's Artifacts panel via tool_context
    artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    try:
        tool_context.save_artifact(filename=filename, artifact=artifact_part)
    except Exception as e:
        print(f"Artifact save warning: {e}")

    # 2. Upload same image bytes to public Cloud Storage bucket
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob_name = f"images/{filename}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"

    return public_url


def generate_apartment_video(
    prompt_description: str,
    tool_context: ToolContext,
) -> str:
    """Generate a short video walkthrough tour for an apartment or neighborhood using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the video to Playground Artifacts and uploads it to public Cloud Storage, returning the public HTTPS URL.

    Args:
        prompt_description: Visual description of the apartment video tour or neighborhood walkthrough (e.g., 'Cinematic short video tour of a sunlit modern 1-bedroom apartment living room').
        tool_context: ToolContext provided by ADK.

    Returns:
        Public HTTPS URL of the generated video in Cloud Storage (https://storage.googleapis.com/<bucket>/<object>).
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

    interaction = client.interactions.create(
        model="gemini-omni-flash-preview",
        input=f"Short video walkthrough tour of an apartment: {prompt_description}",
    )

    output_video = getattr(interaction, "output_video", None)
    if not output_video or not getattr(output_video, "data", None):
        return "Failed to generate video: No video output returned from model."

    data = output_video.data
    if isinstance(data, str):
        video_bytes = base64.b64decode(data)
    else:
        video_bytes = data

    mime_type = getattr(output_video, "mime_type", "video/mp4") or "video/mp4"

    timestamp_id = int(datetime.datetime.now().timestamp())
    filename = f"apartment_tour_{timestamp_id}.mp4"

    # 1. Save artifact to Playground's Artifacts panel via tool_context
    artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
    try:
        tool_context.save_artifact(filename=filename, artifact=artifact_part)
    except Exception as e:
        print(f"Artifact save warning: {e}")

    # 2. Upload same video bytes to public Cloud Storage bucket
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob_name = f"videos/{filename}"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(video_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"

    return public_url


# Configure A2UI Schema Manager with version 0.8 and Basic Catalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are an expert Apartment Finder agent. You remember the user's stated preferences, "
        "health requirements, and all user allergies (such as pet allergies, mold/dust, food, or chemical sensitivities) "
        "from previous conversations and use them to personalize your responses and filter recommendations. "
        "You help users search, explore, and manage rental apartment listings stored in Firestore. "
        "Use search_apartments to find units matching their budget or neighborhood, "
        "get_apartment_details to show full info on a unit, add_apartment_listing when they want to list a new unit, "
        "schedule_tour_request when they want to book an in-person or virtual tour, "
        "lookup_zipcode_location to get ZIP code details, geocode_address to convert addresses into latitude/longitude coordinates, "
        "find_nearby_places to discover local points of interest, generate_apartment_image to generate visual renderings of apartments, "
        "generate_apartment_video to generate video walkthrough tours, "
        "and Python code execution to calculate or solve complex math and logic."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    tools=[
        PreloadMemoryTool(),
        search_apartments,
        get_apartment_details,
        add_apartment_listing,
        schedule_tour_request,
        lookup_zipcode_location,
        geocode_address,
        find_nearby_places,
        generate_apartment_image,
        generate_apartment_video,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)


