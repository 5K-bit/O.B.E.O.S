from __future__ import annotations

import time

import cv2
import typer

from three_thousand.core.camera import capture_frame, is_camera_available, save_frame
from three_thousand.core.config import AppConfig
from three_thousand.core.motion import MotionDetector
from three_thousand.adapters.obeos import publish_motion_spike
from three_thousand.storage.sqlite_store import SQLiteStore
from three_thousand.ui.console import (
    console,
    print_error,
    print_events,
    print_motion_alert,
    print_snapshot_saved,
    print_status,
)

app = typer.Typer(help="3000 local-first camera sentinel.", no_args_is_help=True)


def _build_runtime() -> tuple[AppConfig, SQLiteStore]:
    config = AppConfig()
    config.ensure_paths()
    store = SQLiteStore(config.database_path)
    store.initialize()
    return config, store


@app.command()
def status() -> None:
    """Check whether the configured camera is available."""
    config, _ = _build_runtime()
    try:
        available = is_camera_available(config.camera_index)
    except Exception:
        available = False
    print_status(available)


@app.command()
def snapshot() -> None:
    """Capture and store a single camera frame."""
    config, _ = _build_runtime()
    try:
        frame = capture_frame(config.camera_index)
        if frame is None:
            print_error("Unable to capture frame. Is a camera available?")
            raise typer.Exit(code=1)

        snapshot_path = save_frame(frame, config.snapshots_dir)
        print_snapshot_saved(str(snapshot_path))
    except RuntimeError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc
    except Exception:
        print_error("Snapshot failed unexpectedly.")
        raise typer.Exit(code=1)


@app.command()
def watch(
    interval_seconds: float = typer.Option(0.2, "--interval-seconds", min=0.05),
    motion_threshold: float = typer.Option(0.02, "--motion-threshold", min=0.0, max=1.0),
) -> None:
    """Run motion detection loop and persist local events."""
    config, store = _build_runtime()
    detector = MotionDetector(area_threshold_ratio=motion_threshold)

    try:
        capture = cv2.VideoCapture(config.camera_index)
    except Exception:
        print_error("OpenCV could not initialize camera capture.")
        raise typer.Exit(code=1)

    if not capture or not capture.isOpened():
        if capture:
            capture.release()
        print_error("Camera unavailable. Cannot start watch.")
        raise typer.Exit(code=1)

    console.print("[cyan]Watching camera feed. Press Ctrl+C to stop.[/cyan]")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                print_error("Camera frame read failed. Stopping watch.")
                break

            try:
                result = detector.detect(frame)
                if result.detected:
                    snapshot_path = save_frame(frame, config.snapshots_dir)
                    local_event_id = store.add_event(
                        event_type="motion_detected",
                        confidence=result.confidence,
                        snapshot_path=str(snapshot_path),
                        metadata=result.metadata,
                    )
                    publish_motion_spike(
                        confidence=result.confidence,
                        snapshot_path=str(snapshot_path),
                        metadata=result.metadata,
                        local_event_id=local_event_id,
                    )
                    print_motion_alert(result.confidence, str(snapshot_path))
            except Exception:
                print_error("Frame processing failed; continuing watch loop.")

            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        console.print("[cyan]Watch stopped.[/cyan]")
    except Exception:
        print_error("Watch failed unexpectedly.")
        raise typer.Exit(code=1)
    finally:
        capture.release()


@app.command()
def events(limit: int = typer.Option(20, "--limit", min=1, max=1000)) -> None:
    """List recent motion events."""
    _, store = _build_runtime()
    print_events(store.list_events(limit=limit))


if __name__ == "__main__":
    app()
