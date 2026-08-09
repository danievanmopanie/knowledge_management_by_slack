"""Rebuild the typed frontend-support graph from historical incident CSV files."""

from src.knowledge.support_ingest import seed_incidents
from src.reporting.incidents import load_all_incidents


def main() -> None:
    incidents = load_all_incidents()
    result = seed_incidents(incidents)
    print(
        f"Support graph rebuilt: seeded={result['seeded']} skipped={result['skipped']} "
        f"from {len(incidents)} incident rows"
    )


if __name__ == "__main__":
    main()
