import csv

from src.knowledge.incident_dedupe import content_hash
from src.knowledge.synthetic_day2 import generate_day2_snapshot
from src.reporting.incidents import load_incidents_from_csv


def _write_source(path, number_header="Number"):
    headers = [
        number_header,
        "Short description",
        "Description",
        "Work notes",
        "Resolution notes",
        "State",
        "Assignment group",
        "Updated",
    ]
    rows = []
    for i in range(1, 11):
        rows.append(
            {
                number_header: f"INC{i:04d}",
                "Short description": f"Problem {i}",
                "Description": f"Description {i}",
                "Work notes": f"Work {i}",
                "Resolution notes": f"Resolution {i}",
                "State": "Closed",
                "Assignment group": "Service Desk",
                "Updated": "2026-08-15 10:00:00",
            }
        )
    # Duplicate one incident to prove the generator changes incident identity, not arbitrary rows.
    rows.append(dict(rows[0]))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _unique(path):
    return {inc.number: inc for inc in load_incidents_from_csv(path)}


def test_day2_generator_separates_lifecycle_and_semantic_changes(tmp_path):
    source = tmp_path / "day1.csv"
    output = tmp_path / "day2.csv"
    _write_source(source)

    result = generate_day2_snapshot(
        source,
        output,
        lifecycle_count=2,
        work_notes_count=2,
        resolution_count=2,
        seed=7,
    )

    assert result.changed_unique_incidents == 6
    assert len(result.lifecycle_only) == 2
    assert len(result.work_notes_changed) == 2
    assert len(result.resolution_changed) == 2
    assert output.exists()
    assert output.with_suffix(".manifest.json").exists()

    day1 = _unique(source)
    day2 = _unique(output)
    for number in result.lifecycle_only:
        assert day2[number].assignment_group == "Synthetic Day2 Support"
        assert content_hash(day2[number]) == content_hash(day1[number])
    for number in result.work_notes_changed:
        assert "[Synthetic Day 2]" in day2[number].work_notes
        assert content_hash(day2[number]) != content_hash(day1[number])
    for number in result.resolution_changed:
        assert "[Synthetic Day 2]" in day2[number].resolution_notes
        assert content_hash(day2[number]) != content_hash(day1[number])


def test_day2_generator_infers_incident_number_column_from_inc_values(tmp_path):
    source = tmp_path / "day1_weird_header.csv"
    output = tmp_path / "day2_weird_header.csv"
    _write_source(source, number_header="Record reference exported by ServiceNow")

    parsed = load_incidents_from_csv(source)
    assert len(parsed) == 11
    assert parsed[0].number == "INC0001"
    assert parsed[0].number != parsed[0].short_description

    result = generate_day2_snapshot(
        source,
        output,
        lifecycle_count=2,
        work_notes_count=2,
        resolution_count=2,
        seed=7,
    )
    assert result.unique_incidents == 10
    assert result.changed_unique_incidents == 6
