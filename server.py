#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pyarrow.parquet as pq


DATASET_RE = re.compile(r"^(20\d{6})_lerobot_v(?:2(?:\.1)?|21)$")
SKIP_DIRS = {".git", ".venv", "__pycache__", "logs", "checkpoints", "outputs", "wandb", "trash"}
JOINT_LABELS = {
    **{f"left_arm_j{i}": f"左臂关节{i}" for i in range(1, 8)},
    **{f"right_arm_j{i}": f"右臂关节{i}" for i in range(1, 8)},
    "left_xhand_thumb_bend": "左手拇指弯曲",
    "left_xhand_thumb_rota1": "左手拇指旋转1",
    "left_xhand_thumb_rota2": "左手拇指旋转2",
    "left_xhand_index_bend": "左手食指弯曲",
    "left_xhand_index_j1": "左手食指关节1",
    "left_xhand_index_j2": "左手食指关节2",
    "left_xhand_mid_j1": "左手中指关节1",
    "left_xhand_mid_j2": "左手中指关节2",
    "left_xhand_ring_j1": "左手无名指关节1",
    "left_xhand_ring_j2": "左手无名指关节2",
    "left_xhand_pinky_j1": "左手小指关节1",
    "left_xhand_pinky_j2": "左手小指关节2",
    "right_xhand_thumb_bend": "右手拇指弯曲",
    "right_xhand_thumb_rota1": "右手拇指旋转1",
    "right_xhand_thumb_rota2": "右手拇指旋转2",
    "right_xhand_index_bend": "右手食指弯曲",
    "right_xhand_index_j1": "右手食指关节1",
    "right_xhand_index_j2": "右手食指关节2",
    "right_xhand_mid_j1": "右手中指关节1",
    "right_xhand_mid_j2": "右手中指关节2",
    "right_xhand_ring_j1": "右手无名指关节1",
    "right_xhand_ring_j2": "右手无名指关节2",
    "right_xhand_pinky_j1": "右手小指关节1",
    "right_xhand_pinky_j2": "右手小指关节2",
}


@dataclass(frozen=True)
class Source:
    category: str
    name: str
    root: Path


@dataclass
class Dataset:
    id: str
    source: Source
    path: Path
    info: dict
    episodes: list[dict]

    @property
    def date(self) -> str:
        match = DATASET_RE.match(self.path.name)
        return match.group(1) if match else self.path.name

    @property
    def fps(self) -> float:
        return float(self.info.get("fps") or 0)

    @property
    def cameras(self) -> list[dict]:
        result = []
        for key, feature in self.info.get("features", {}).items():
            if feature.get("dtype") == "video":
                result.append({"key": key, "label": camera_label(key), "shape": feature.get("shape", [])})
        return result


def camera_label(key: str) -> str:
    labels = {
        "observation.images.ego_view": "头部视角",
        "observation.images.left_wrist": "左腕视角",
        "observation.images.right_wrist": "右腕视角",
    }
    return labels.get(key, key.rsplit(".", 1)[-1])


def safe_duration(frames: int, fps: float) -> float:
    return frames / fps if fps > 0 else 0.0


class Catalog:
    def __init__(self, sources: list[Source]):
        self.sources = sources
        self.lock = threading.RLock()
        self.by_id: dict[str, Dataset] = {}
        self.last_scan = 0.0
        self.refresh()

    def _roots(self, source: Source):
        root = source.root
        if not root.exists():
            return
        for current, dirs, files in os.walk(root, followlinks=True):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            if "meta" in dirs and (current_path / "meta/info.json").is_file():
                yield current_path
                dirs[:] = []
                continue
            if depth >= 5:
                dirs[:] = []

    def refresh(self) -> None:
        found: dict[str, Dataset] = {}
        seen_paths: set[Path] = set()
        for source_index, source in enumerate(self.sources):
            for path in self._roots(source) or []:
                real_path = path.resolve()
                if real_path in seen_paths:
                    continue
                try:
                    info = json.loads((path / "meta/info.json").read_text())
                    episode_file = path / "meta/episodes.jsonl"
                    episodes = [json.loads(line) for line in episode_file.read_text().splitlines() if line.strip()]
                except (OSError, json.JSONDecodeError):
                    continue
                identity = f"{source_index}\0{path.absolute()}"
                dataset_id = hashlib.sha1(identity.encode()).hexdigest()[:14]
                found[dataset_id] = Dataset(dataset_id, source, path.absolute(), info, episodes)
                seen_paths.add(real_path)
        with self.lock:
            self.by_id = found
            self.last_scan = time.time()

    def maybe_refresh(self, force: bool = False) -> None:
        if force or time.time() - self.last_scan > 60:
            self.refresh()

    def get(self, dataset_id: str) -> Dataset | None:
        with self.lock:
            return self.by_id.get(dataset_id)

    def response(self) -> dict:
        with self.lock:
            datasets = sorted(self.by_id.values(), key=lambda d: (d.source.category, d.source.name, d.date, d.path.name))
        rows = [dataset_summary(d) for d in datasets]
        total_frames = sum(x["frames"] for x in rows)
        return {
            "datasets": rows,
            "summary": {
                "datasets": len(rows),
                "episodes": sum(x["episodes"] for x in rows),
                "frames": total_frames,
                "duration_seconds": sum(x["duration_seconds"] for x in rows),
                "categories": sorted({x["category"] for x in rows}),
            },
            "scanned_at": self.last_scan,
        }


def dataset_summary(dataset: Dataset) -> dict:
    info = dataset.info
    frames = int(info.get("total_frames") or sum(int(r.get("length", 0)) for r in dataset.episodes))
    fps = dataset.fps
    durations = [safe_duration(int(row.get("length", 0)), fps) for row in dataset.episodes]
    validation = "未提供"
    validation_path = dataset.path / "meta/validation.json"
    try:
        validation = json.loads(validation_path.read_text()).get("status", "未知")
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "id": dataset.id,
        "category": dataset.source.category,
        "source": dataset.source.name,
        "date": dataset.date,
        "name": dataset.path.name,
        "path": str(dataset.path),
        "episodes": len(dataset.episodes),
        "frames": frames,
        "fps": fps,
        "duration_seconds": safe_duration(frames, fps),
        "episode_duration": {
            "min": min(durations, default=0),
            "max": max(durations, default=0),
            "average": sum(durations) / len(durations) if durations else 0,
        },
        "validation": validation,
        "cameras": dataset.cameras,
        "joints": joint_names(dataset),
    }


def joint_names(dataset: Dataset) -> list[str]:
    features = dataset.info.get("features", {})
    state = features.get("observation.state", {})
    action = features.get("action", {})
    names = state.get("names") or action.get("names") or []
    count = (state.get("shape") or action.get("shape") or [0])[0]
    return list(names) if names else [f"joint_{i}" for i in range(int(count))]


def joint_labels(dataset: Dataset) -> list[str]:
    return [JOINT_LABELS.get(name, name) for name in joint_names(dataset)]


def episode_row(dataset: Dataset, episode: int) -> dict | None:
    for row in dataset.episodes:
        if int(row.get("episode_index", -1)) == episode:
            return dict(row)
    return None


def format_path(dataset: Dataset, template_key: str, episode: int, video_key: str | None = None) -> Path:
    chunk_size = int(dataset.info.get("chunks_size") or 1000)
    template = dataset.info.get(template_key)
    if not template:
        if template_key == "data_path":
            template = "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
        else:
            template = "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
    relative = template.format(episode_chunk=episode // chunk_size, episode_index=episode, video_key=video_key)
    candidate = dataset.path / relative
    resolved_root = dataset.path.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise ValueError("invalid dataset path")
    return candidate


def finite(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def curve_payload(dataset: Dataset, episode: int, max_points: int) -> dict:
    parquet_path = format_path(dataset, "data_path", episode)
    columns = [name for name in ("timestamp", "observation.state", "action") if name in pq.read_schema(parquet_path).names]
    table = pq.read_table(parquet_path, columns=columns)
    count = len(table)
    step = max(1, math.ceil(count / max_points))
    indices = list(range(0, count, step))
    if count and indices[-1] != count - 1:
        indices.append(count - 1)
    timestamps = table["timestamp"].to_pylist() if "timestamp" in columns else [i / dataset.fps for i in range(count)]
    state = table["observation.state"].to_pylist() if "observation.state" in columns else []
    action = table["action"].to_pylist() if "action" in columns else []
    return {
        "episode_index": episode,
        "original_points": count,
        "sampled_points": len(indices),
        "names": joint_names(dataset),
        "labels": joint_labels(dataset),
        "timestamps": [finite(timestamps[i]) for i in indices],
        "state": [[finite(v) for v in state[i]] for i in indices] if state else [],
        "action": [[finite(v) for v in action[i]] for i in indices] if action else [],
    }


class ReplayHandler(BaseHTTPRequestHandler):
    server_version = "LeRobotReplay/2.0"

    @property
    def catalog(self) -> Catalog:
        return self.server.catalog  # type: ignore[attr-defined]

    @property
    def web_root(self) -> Path:
        return self.server.web_root  # type: ignore[attr-defined]

    def json_response(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error_json(self, status: int, message: str) -> None:
        self.json_response({"error": message}, status)

    def send_file(self, path: Path, allow_range: bool = False) -> None:
        size = path.stat().st_size
        start, end, status = 0, size - 1, HTTPStatus.OK
        range_header = self.headers.get("Range") if allow_range else None
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            left, right = match.groups()
            if left:
                start, end = int(left), int(right) if right else end
            elif right:
                start = max(0, size - int(right))
            if start >= size or end < start:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            end, status = min(end, size - 1), HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600" if allow_range else "no-cache")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        try:
            if parsed.path == "/api/health":
                self.catalog.maybe_refresh()
                summary = self.catalog.response()["summary"]
                self.json_response({"status": "ok", **summary})
                return
            if parsed.path == "/api/datasets":
                query = parse_qs(parsed.query)
                self.catalog.maybe_refresh(query.get("refresh", ["0"])[0] == "1")
                self.json_response(self.catalog.response())
                return
            if len(parts) >= 3 and parts[:2] == ["api", "datasets"]:
                dataset = self.catalog.get(parts[2])
                if dataset is None:
                    self.error_json(404, "dataset not found")
                    return
                if len(parts) == 4 and parts[3] == "episodes":
                    query = parse_qs(parsed.query)
                    offset = max(0, int(query.get("offset", [0])[0]))
                    limit = min(1000, max(1, int(query.get("limit", [500])[0])))
                    rows = []
                    for item in dataset.episodes[offset:offset + limit]:
                        row = dict(item)
                        row["duration_seconds"] = safe_duration(int(row.get("length", 0)), dataset.fps)
                        rows.append(row)
                    self.json_response({"total": len(dataset.episodes), "episodes": rows})
                    return
                if len(parts) == 5 and parts[3] == "episode":
                    episode = int(parts[4])
                    row = episode_row(dataset, episode)
                    if row is None:
                        self.error_json(404, "episode not found")
                        return
                    videos = []
                    for camera_index, camera in enumerate(dataset.cameras):
                        path = format_path(dataset, "video_path", episode, camera["key"])
                        videos.append({**camera, "url": f"/media/{dataset.id}/{camera_index}/{episode}.mp4", "available": path.is_file()})
                    row.update({
                        "duration_seconds": safe_duration(int(row.get("length", 0)), dataset.fps),
                        "fps": dataset.fps,
                        "videos": videos,
                        "joints": joint_names(dataset),
                        "curves_url": f"/api/datasets/{dataset.id}/episode/{episode}/curves",
                    })
                    self.json_response(row)
                    return
                if len(parts) == 6 and parts[3] == "episode" and parts[5] == "curves":
                    episode = int(parts[4])
                    if episode_row(dataset, episode) is None:
                        self.error_json(404, "episode not found")
                        return
                    query = parse_qs(parsed.query)
                    max_points = min(5000, max(100, int(query.get("max_points", [1600])[0])))
                    self.json_response(curve_payload(dataset, episode, max_points))
                    return
            if len(parts) == 4 and parts[0] == "media" and parts[3].endswith(".mp4"):
                dataset = self.catalog.get(parts[1])
                camera_index, episode = int(parts[2]), int(parts[3][:-4])
                if dataset is None or camera_index < 0 or camera_index >= len(dataset.cameras):
                    self.send_error(404)
                    return
                path = format_path(dataset, "video_path", episode, dataset.cameras[camera_index]["key"])
                if not path.is_file():
                    self.send_error(404)
                    return
                self.send_file(path, allow_range=True)
                return
            relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            if relative != "index.html":
                self.send_error(404)
                return
            self.send_file(self.web_root / relative)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError as exc:
            self.error_json(404, str(exc))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.error_json(500, str(exc))

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)


def parse_source(value: str) -> Source:
    try:
        category, name, path = value.split("=", 2)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("source format: 分类=名称=/path") from exc
    return Source(category.strip(), name.strip(), Path(path).expanduser().absolute())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", type=parse_source, required=True, help="分类=名称=/path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7865)
    args = parser.parse_args()
    catalog = Catalog(args.source)
    server = ThreadingHTTPServer((args.host, args.port), ReplayHandler)
    server.catalog = catalog  # type: ignore[attr-defined]
    server.web_root = Path(__file__).resolve().parent / "web"  # type: ignore[attr-defined]
    print(f"DataReplay listening on http://{args.host}:{args.port}; {len(catalog.by_id)} datasets", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
