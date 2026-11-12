"""Optional YOLO-based hazard inference adapter for AegisScan AI.

The engine intentionally returns 'not configured' when model weights or a
hazard-label allow-list are missing. It never fabricates detections.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


class AegisVisionEngine:
    def __init__(self) -> None:
        self.model_path = Path(os.getenv("AEGIS_MODEL_PATH", "models/hazard-vision.pt"))
        self.model_name = os.getenv("AEGIS_MODEL_NAME", "AegisScan Hazard Vision")
        self.model_version = os.getenv("AEGIS_MODEL_VERSION", "2.0.0")
        self.device = os.getenv("AEGIS_MODEL_DEVICE", "cpu")
        self.confidence = float(os.getenv("AEGIS_MODEL_CONFIDENCE", "0.35"))
        self.hazard_labels = {
            item.strip().lower()
            for item in os.getenv(
                "AEGIS_HAZARD_LABELS",
                "fire,smoke,spill,no_helmet,no_vest,blocked_exit,unsafe_ladder",
            ).split(",")
            if item.strip()
        }
        self._model: Any = None
        self._load_error: str | None = None
        self._attempted_load = False

    def _load(self) -> None:
        if self._attempted_load:
            return
        self._attempted_load = True
        if not self.model_path.is_file():
            self._load_error = f"Model weights not found: {self.model_path}"
            return
        if not self.hazard_labels:
            self._load_error = "AEGIS_HAZARD_LABELS is empty; configure approved hazard classes."
            return
        try:
            from ultralytics import YOLO  # optional dependency
            self._model = YOLO(str(self.model_path))
        except Exception as exc:  # keep service available even if the optional model cannot load
            self._load_error = f"Could not load the AI model: {type(exc).__name__}: {exc}"

    def status(self) -> dict[str, Any]:
        self._load()
        ready = self._model is not None
        names: list[str] = []
        if ready:
            model_names = getattr(self._model, "names", {}) or {}
            names = list(model_names.values()) if isinstance(model_names, dict) else list(model_names)
        return {
            "ready": ready,
            "name": self.model_name,
            "version": self.model_version,
            "weights_path": str(self.model_path),
            "device": self.device,
            "confidence_threshold": self.confidence,
            "configured_hazard_labels": sorted(self.hazard_labels),
            "model_classes": names,
            "message": "AI model ready." if ready else (self._load_error or "AI model is not configured."),
        }

    def analyze(self, image_path: Path) -> dict[str, Any]:
        self._load()
        if self._model is None:
            return {
                "ready": False,
                "detections": [],
                "inference_ms": 0,
                "message": self._load_error or "AI model is not configured.",
                "model_name": self.model_name,
                "model_version": self.model_version,
            }

        started = time.perf_counter()
        results = self._model.predict(
            source=str(image_path),
            conf=self.confidence,
            device=self.device,
            verbose=False,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        detections: list[dict[str, Any]] = []

        for result in results or []:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            names = getattr(result, "names", {}) or {}
            for box in boxes:
                class_id = int(box.cls[0].item())
                label = str(names.get(class_id, class_id)).strip()
                normalized = label.lower().replace(" ", "_").replace("-", "_")
                if normalized not in self.hazard_labels:
                    continue
                score = float(box.conf[0].item())
                xyxy = [float(value) for value in box.xyxy[0].tolist()]
                detections.append({
                    "label": label,
                    "normalized_label": normalized,
                    "confidence": round(score, 4),
                    "bbox": [round(value, 2) for value in xyxy],
                })

        return {
            "ready": True,
            "detections": detections,
            "inference_ms": elapsed_ms,
            "message": "Inference completed.",
            "model_name": self.model_name,
            "model_version": self.model_version,
        }
