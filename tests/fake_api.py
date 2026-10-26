"""Made-up Paikannuspalvelu responses (no real routes, stops or people)."""

API = "https://api.paikannuspalvelu.fi"
DATA_KEY = "test-key"

ROUTES = [
    {"name": "REITTI 99_100_1 pe kevät", "file": 9001, "zips": ["00990", "00991"]},
    {"name": "REITTI 99_200_2 ma", "file": 9002, "zips": ["00990"]},
    {"name": "REITTI 98_300_1 ti", "file": 9003, "zips": ["01230"]},
    {"name": "Huoltoajo", "file": 9004, "zips": ["00990"]},
]


def stop_feature(seq, name, lat, lon, schedules, zip_code="00990"):
    return {
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "sequence_on_route": seq,
            "name_on_route": name,
            "zip": zip_code,
            "contact": "040-0000000",
            "schedules": schedules,
        },
    }


# Route 9001: four stops eastwards from (60.0, 25.0), 200 m apart.
STOPS_9001 = {
    "features": [
        stop_feature(2, "Keksitie 3", 60.0, 25.00718, ["pe 4.6.2027 klo: 17:10", "pe 18.6.2027 klo: 17:10"]),
        stop_feature(1, "Keksitie 1", 60.0, 25.00359, ["pe 4.6.2027 klo: 17:05", "pe 18.6.2027 klo: 17:05"]),
        stop_feature(0, "Alkupiste", 60.0, 25.0, ["pe 4.6.2027 klo: 17:00", "pe 18.6.2027 klo: 17:00"]),
        stop_feature(3, "Kuvitelmankuja", 60.0, 25.01077, ["pe 4.6.2027 klo: 17:15", "pe 18.6.2027 klo: 17:15", "bad"]),
    ]
}
STOPS_9002 = {
    "features": [
        stop_feature(0, "Toinen reitti 5", 60.001, 25.0036, ["ma 7.6.2027 klo: 18:00"]),
    ]
}
DEVICES = {
    "features": [
        {"geometry": {"coordinates": [25.004, 60.0]}, "properties": {"id": 77, "name": "Auto 1", "speed": 12, "heading": 90, "timestamp": "2027-06-04T17:06:00+03:00"}},
        {"geometry": {"coordinates": []}, "properties": {"id": 78}},
    ]
}


def mock_api(aioclient_mock, routes=ROUTES):
    aioclient_mock.get(f"{API}/v1/public/route/", json=routes)
    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9001"}, json=STOPS_9001)
    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9002"}, json=STOPS_9002)
    aioclient_mock.get(f"{API}/v1/public/device/", json=DEVICES)
