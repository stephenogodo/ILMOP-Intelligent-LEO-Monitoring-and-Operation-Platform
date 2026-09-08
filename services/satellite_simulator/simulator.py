import csv
import time
from pathlib import Path

from services.satellite_simulator.telemetry import (
    TelemetryGenerator,
)


class SatelliteSimulator:
    def __init__(self):
        self.generator = TelemetryGenerator()

    def run(
        self,
        samples=10,
        interval_seconds=1,
        save_csv=True,
    ):
        writer = None
        csv_file = None

        if save_csv:
            Path("data/telemetry").mkdir(
                parents=True,
                exist_ok=True,
            )

            from datetime import datetime, UTC

            timestamp = datetime.now(UTC)

            filename = (
            "data/telemetry/"
            f"telemetry_{timestamp:%Y%m%d_%H%M%S}.csv"
                       )

            csv_file = open(
                filename,
                "w",
                newline="",
                encoding="utf-8",
            )

        for i in range(samples):
            telemetry = self.generator.generate()

            record = telemetry.model_dump()

            print(f"\nSample {i+1}")
            print(record)

            if save_csv:
                if writer is None:
                    writer = csv.DictWriter(
                        csv_file,
                        fieldnames=record.keys(),
                    )
                    writer.writeheader()

                writer.writerow(record)

            time.sleep(interval_seconds)

        if csv_file:
            csv_file.close()

            print(
                "\nTelemetry saved to:"
            )
            print(filename)