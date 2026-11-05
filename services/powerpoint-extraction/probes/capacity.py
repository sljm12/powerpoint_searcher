"""Run a bounded, authenticated EX1 HTTP capacity check on Linux."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(path: Path) -> tuple[bytes, str]:
    image = io.BytesIO()
    Image.new("RGB", (160, 120), (40, 120, 200)).save(image, format="PNG")
    picture = image.getvalue()
    deck = Presentation()
    for index in range(20):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        shape = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(8), Inches(0.5))
        shape.name = f"capacity-text-{index}"
        shape.text_frame.text = f"Quarterly report slide {index + 1}"
        shape = slide.shapes.add_picture(io.BytesIO(picture), Inches(0.5), Inches(1), Inches(2), Inches(1.5))
        shape.name = f"capacity-picture-{index}"
        if index == 0:
            table_shape = slide.shapes.add_table(
                2, 2, Inches(3), Inches(1), Inches(3), Inches(1)
            )
            table_shape.name = "capacity-table"
            table = table_shape.table
            for cell, value in zip((c for row in table.rows for c in row.cells), ("Region", "Value", "North", "42"), strict=True):
                cell.text = value
            data = CategoryChartData()
            data.categories = ["First", "Second"]
            data.add_series("Measure", (3, 7))
            chart = slide.shapes.add_chart(
                XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(3), Inches(2.5), Inches(4), Inches(2), data
            )
            chart.name = "capacity-chart"
            group = slide.shapes.add_group_shape()
            group.name = "capacity-group"
            child = group.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7), Inches(1), Inches(1), Inches(1))
            child.name = "capacity-group-child"
    deck.save(str(path))
    data = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        embedded = [archive.read(name) for name in archive.namelist() if name.startswith("ppt/media/")]
    assert embedded and picture in embedded
    return data, sha(picture)


def disk_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def memory_sample(pid: int) -> tuple[int, int]:
    parents: dict[int, int] = {}
    rss: dict[int, int] = {}
    renderer_cgroups: set[str] = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            status = (proc / "status").read_text()
            values = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
            parents[int(proc.name)] = int(values["PPid"].strip())
            rss[int(proc.name)] = int(values.get("VmRSS", "0").split()[0]) * 1024
            cgroup = (proc / "cgroup").read_text().split("::", 1)[-1].strip()
            if "ex1-render-" in cgroup:
                renderer_cgroups.add(cgroup)
        except (OSError, KeyError, ValueError, IndexError):
            continue
    family = {pid}
    while True:
        expanded = family | {child for child, parent in parents.items() if parent in family}
        if expanded == family:
            server_rss = sum(rss.get(p, 0) for p in family)
            renderer_memory = 0
            for cgroup in renderer_cgroups:
                try:
                    renderer_memory += int(
                        (Path("/sys/fs/cgroup") / cgroup.lstrip("/") / "memory.current")
                        .read_text()
                        .strip()
                    )
                except (OSError, ValueError):
                    continue
            return server_rss, renderer_memory
        family = expanded


class Probe:
    def __init__(self, base: str, token: str, out: Path, timeout: float):
        self.base, self.token, self.out, self.timeout = base.rstrip("/"), token, out, timeout
        self.requests = 0

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None, expected: tuple[int, ...] = (200,)) -> tuple[bytes, object]:
        assert path.startswith("/") and not path.startswith("//")
        headers = {"Authorization": "Bearer " + self.token}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        self.requests += 1
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            if response.status not in expected:
                raise RuntimeError(f"{method} {path} returned {response.status}: {data[:500]!r}")
            return data, response.headers

    def json(self, method: str, path: str, **kwargs: object) -> dict:
        data, _ = self.request(method, path, **kwargs)
        return json.loads(data)

    def poll(self, path: str, done: str, deadline: float) -> tuple[dict, list[dict]]:
        observations = []
        while time.monotonic() < deadline:
            value = self.json("GET", path)
            observations.append({"seconds": time.monotonic(), "state": value["state"], "stage": value.get("stage")})
            if value["state"] == done:
                return value, observations
            if value["state"] == "failed" or value.get("error"):
                raise RuntimeError(f"{path} failed: {value}")
            time.sleep(0.5)
        raise TimeoutError(f"deadline polling {path}")

    def run_deck(self, deck: str, blob: bytes, picture_sha: str, deadline_seconds: float) -> dict:
        result: dict = {"deck_id": deck, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        start = time.monotonic()
        try:
            boundary = "capacity-" + uuid.uuid4().hex
            chunks = []
            for name, value in (("source_revision", "1"), ("idempotency_key", deck)):
                chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"capacity.pptx\"\r\nContent-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation\r\n\r\n".encode() + blob + b"\r\n")
            chunks.append(f"--{boundary}--\r\n".encode())
            job = self.json("POST", f"/v1/decks/{deck}/extractions", body=b"".join(chunks), content_type=f"multipart/form-data; boundary={boundary}", expected=(202,))
            result["job_id"] = job["job_id"]
            job, observations = self.poll(job["status_url"], "succeeded", start + deadline_seconds)
            result["observations"] = [{**o, "seconds": o["seconds"] - start} for o in observations]
            result["job"] = job
            manifest = self.json("GET", job["manifest_url"])
            assert manifest["deck_id"] == deck and manifest["source_sha256"] == sha(blob)
            slides = manifest["content"]["slides"]
            assert [s["source"]["number"] for s in slides] == list(range(1, 21))
            assets = {a["local_id"]: a for a in manifest["assets"]}
            assert len(assets) == len(manifest["assets"])
            source_deck = Presentation(io.BytesIO(blob))
            def shape_names(shapes: object) -> set[str]:
                names = set()
                for shape in shapes:
                    names.add(shape.name)
                    if shape.shape_type == 6:
                        names.update(shape_names(shape.shapes))
                return names
            names = set()
            for slide, source_slide in zip(slides, source_deck.slides, strict=True):
                preview = slide["preview"]
                assert preview["status"] == "rendered" and assets[preview["asset_local_id"]]["role"] == "slide_preview"
                slide_names = {item["source"]["source_name"] for item in slide["occurrences"]}
                assert shape_names(source_slide.shapes) <= slide_names
                for occurrence in slide["occurrences"]:
                    names.add(occurrence["source"]["source_name"])
                    rendition = occurrence["rendition"]
                    assert rendition["status"] == "not_visible" or rendition["asset_local_id"] in assets
                    if occurrence["source"]["source_name"].startswith("capacity-picture-"):
                        assert assets[occurrence["original_asset_local_id"]]["sha256"] == picture_sha
            assert {f"capacity-text-{i}" for i in range(20)} <= names
            assert {f"capacity-picture-{i}" for i in range(20)} <= names
            assert {"capacity-group", "capacity-group-child"} <= names
            first_slide = slides[0]["occurrences"]
            by_name = {item["source"]["source_name"]: item["source"] for item in first_slide}
            assert by_name["capacity-table"]["kind"] == "table"
            assert all(value in by_name["capacity-table"]["text"] for value in ("Region", "North", "42"))
            assert by_name["capacity-chart"]["kind"] == "chart"
            assert all(value in by_name["capacity-chart"]["text"] for value in ("First", "Second", "Measure"))
            occurrences_by_name = {
                item["source"]["source_name"]: item for item in first_slide
            }
            assert (
                occurrences_by_name["capacity-group-child"]["source"]["parent_id"]
                == occurrences_by_name["capacity-group"]["source"]["id"]
            )
            pictures = [
                occurrence
                for slide in slides
                for occurrence in slide["occurrences"]
                if occurrence["source"]["source_name"].startswith("capacity-picture-")
            ]
            assert len(pictures) == 20
            assert len({item["source"]["id"] for item in pictures}) == 20
            assert len({item["original_asset_local_id"] for item in pictures}) == 1
            assert all(item["rendition"]["status"] == "rendered" for item in pictures)
            output_bytes = 0
            for asset in assets.values():
                data, headers = self.request("GET", asset["download_url"])
                assert len(data) == asset["byte_length"] and sha(data) == asset["sha256"]
                assert headers.get_content_type() == asset["media_type"]
                if asset["media_type"] == "image/png":
                    assert data.startswith(b"\x89PNG\r\n\x1a\n")
                    with Image.open(io.BytesIO(data)) as image:
                        image.load()
                        assert list(image.size) == asset["pixel_size"]
                output_bytes += len(data)
            result.update(ok=True, output_bytes=output_bytes, asset_count=len(assets), old_asset_url=next(iter(assets.values()))["download_url"], elapsed_seconds=time.monotonic() - start)
        except Exception as error:
            result.update(ok=False, error=f"{type(error).__name__}: {error}", elapsed_seconds=time.monotonic() - start)
        (self.out / f"{deck}.json").write_text(json.dumps(result, indent=2))
        return result

    def delete(self, result: dict, deadline: float) -> None:
        deck = result["deck_id"]
        self.json("DELETE", f"/v1/decks/{deck}", expected=(202,))
        self.poll(f"/v1/decks/{deck}/deletion", "complete", time.monotonic() + deadline)
        self.request("GET", result["old_asset_url"], expected=(404, 410))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service-pid", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--conditions", required=True, help="Describe service startup and cache state")
    parser.add_argument("--deadline", type=float, default=240)
    args = parser.parse_args()
    token = os.environ["EX1_TOKEN"]
    args.output.mkdir(parents=True, exist_ok=False)
    blob, picture_sha = fixture(args.output / "fixture.pptx")
    probe = Probe(args.url, token, args.output, 30)
    stop = threading.Event()
    samples: list[tuple[int, int]] = []
    def sample() -> None:
        while not stop.is_set():
            samples.append(memory_sample(args.service_pid))
            stop.wait(0.5)
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    report: dict = {
        "conditions": args.conditions,
        "platform": platform.platform(),
        "capacity_probe_sha256": sha(Path(__file__).read_bytes()),
        "source_snapshot_sha256": sha(Path(__file__).with_name("source-sha256.txt").read_bytes()),
        "fixture_sha256": sha(blob),
        "picture_sha256": picture_sha,
        "poll_interval_seconds": 0.5,
        "memory_sample_interval_seconds": 0.5,
        "start_data_bytes": disk_size(args.data_dir),
        "load_average": os.getloadavg(),
        "cpu_count": os.cpu_count(),
        "results": [],
    }
    try:
        for index in range(105):
            result = probe.run_deck(str(uuid.uuid4()), blob, picture_sha, args.deadline)
            report["results"].append(result)
            (args.output / "summary.json").write_text(json.dumps(report, indent=2))
            if not result["ok"]:
                raise RuntimeError(f"job {index + 1} failed; evidence retained")
            if index < 5:
                probe.delete(result, args.deadline)
        report["retained_data_bytes"] = disk_size(args.data_dir)
        for result in report["results"][5:]:
            probe.delete(result, args.deadline)
        report["after_delete_data_bytes"] = disk_size(args.data_dir)
        assert report["after_delete_data_bytes"] < report["retained_data_bytes"]
        time.sleep(2)
        report["after_delete_wait_data_bytes"] = disk_size(args.data_dir)
        assert report["after_delete_wait_data_bytes"] <= report["after_delete_data_bytes"], "data directory grew after deletion"
        times = [r["elapsed_seconds"] for r in report["results"][:5]]
        report.update(repeat_median_seconds=statistics.median(times), repeat_range_seconds=[min(times), max(times)], ok=True)
    finally:
        stop.set()
        thread.join()
        report.update(
            sampled_peak_service_descendant_rss_bytes=max((sample[0] for sample in samples), default=0),
            sampled_peak_renderer_scope_memory_bytes=max(
                (sample[1] for sample in samples), default=0
            ),
            sampled_peak_combined_memory_bytes=max(
                (sum(sample) for sample in samples), default=0
            ),
            requests=probe.requests,
        )
        (args.output / "summary.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

