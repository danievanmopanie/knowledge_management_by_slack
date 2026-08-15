import pytest

from src.knowledge.file_loader import UploadValidationError, validate_slack_file


MIB = 1024 * 1024


def test_large_incident_csv_requires_create_knowledge_override():
    file_info = {
        "id": "F123",
        "name": "incident (Jul).csv",
        "size": 33 * MIB,
    }

    with pytest.raises(UploadValidationError):
        validate_slack_file(file_info)

    safe_name, suffix = validate_slack_file(file_info, max_bytes=100 * MIB)

    assert suffix == ".csv"
    assert safe_name.startswith("F123_")
    assert safe_name.endswith(".csv")
