"""Seed script for Firestore database with apartment listings."""
import os
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-02-b67103ed1a1b"

SEED_APARTMENTS = [
    {
        "id": "apt-101",
        "title": "Sunlit Modern Loft",
        "neighborhood": "Downtown",
        "bedrooms": 1,
        "bathrooms": 1.0,
        "monthly_rent": 2400,
        "pet_friendly": True,
        "amenities": ["In-unit Laundry", "Balcony", "Fitness Center"],
        "description": "Bright open-concept loft with floor-to-ceiling windows and city skyline views.",
        "available": True,
    },
    {
        "id": "apt-102",
        "title": "Spacious 2BR Near Park",
        "neighborhood": "Central Park",
        "bedrooms": 2,
        "bathrooms": 2.0,
        "monthly_rent": 3600,
        "pet_friendly": True,
        "amenities": ["Dishwasher", "Doorman", "Roof Deck", "Parking"],
        "description": "Quiet residential 2-bedroom steps away from green spaces and subway lines.",
        "available": True,
    },
    {
        "id": "apt-103",
        "title": "Cozy Arts District Studio",
        "neighborhood": "Arts District",
        "bedrooms": 0,
        "bathrooms": 1.0,
        "monthly_rent": 1850,
        "pet_friendly": False,
        "amenities": ["Hardwood Floors", "High-speed Internet Included"],
        "description": "Charming studio with exposed brick walls, updated kitchen, and great local cafes.",
        "available": True,
    },
    {
        "id": "apt-104",
        "title": "Luxury Waterfront Penthouse",
        "neighborhood": "Waterfront",
        "bedrooms": 3,
        "bathrooms": 2.5,
        "monthly_rent": 5200,
        "pet_friendly": True,
        "amenities": ["Private Elevator", "Pool", "Storage Unit", "Concierge"],
        "description": "Top-floor penthouse featuring panoramic bay views and top-of-the-line appliances.",
        "available": True,
    },
]


def seed_database():
    print(f"Connecting to Firestore for project '{PROJECT_ID}'...")
    db = firestore.Client(project=PROJECT_ID, database="(default)")
    collection_ref = db.collection("apartments")

    for apartment in SEED_APARTMENTS:
        doc_id = apartment["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(apartment)
        print(f"Seeded apartment listing '{doc_id}': {apartment['title']}")

    print("Firestore database seeding completed successfully!")


if __name__ == "__main__":
    seed_database()
