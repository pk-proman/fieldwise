"""Pluggable face verification.
- mock: accepts any selfie once enrolled; stores the enrolment photo. NOT secure - dev/demo only.
- rekognition: AWS Rekognition CompareFaces against the enrolled reference (needs boto3 + AWS creds).
Fingerprint is performed on-device (WebAuthn / Capacitor biometric); the client reports device_biometric=True
and the server records it. Server cannot verify a fingerprint itself."""
from . import config


def verified() -> bool:
    """True only when a real face-matching provider is configured. With the 'mock' provider a selfie is stored as photo proof but is NOT identity verification."""
    return config.BIOMETRIC_PROVIDER != "mock"


def compare(reference: bytes, probe: bytes) -> tuple[bool, float]:
    if config.BIOMETRIC_PROVIDER == "rekognition":
        import boto3  # lazy
        client = boto3.client("rekognition")
        r = client.compare_faces(SourceImage={"Bytes": reference}, TargetImage={"Bytes": probe}, SimilarityThreshold=85)
        sim = max((m["Similarity"] for m in r.get("FaceMatches", [])), default=0.0)
        return sim >= 85, sim
    return True, 100.0
