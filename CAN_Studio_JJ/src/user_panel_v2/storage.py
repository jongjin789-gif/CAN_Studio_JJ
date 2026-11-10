from src.bus_config import BUS_IDS
import json
import os
import tempfile
import zipfile


PACKAGE_EXT = ".pjjupkg"
PACKAGE_FORMAT = "PJJ_USER_PANEL_PACKAGE"


def save_panel_json(path, panel_data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(panel_data, f, indent=2)


def load_panel_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_bundle(path, panel_data, db_paths_by_bus):
    if not path.lower().endswith(PACKAGE_EXT):
        path += PACKAGE_EXT

    manifest = {
        "format": PACKAGE_FORMAT,
        "version": 2,
        "db_files": {str(b): [] for b in BUS_IDS},
    }

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("panel/panel.json", json.dumps(panel_data, indent=2))

        for bus in BUS_IDS:
            paths = db_paths_by_bus.get(bus, []) if isinstance(db_paths_by_bus, dict) else []
            for i, src in enumerate(paths):
                if not src or not os.path.exists(src):
                    continue
                base = os.path.basename(src)
                arc_name = f"db/bus{bus}/{i:03d}_{base}"
                zf.write(src, arcname=arc_name)
                manifest["db_files"][str(bus)].append(
                    {
                        "arc_name": arc_name,
                        "name": base,
                    }
                )

        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return path


def load_bundle(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    temp_dir = tempfile.mkdtemp(prefix="pjjupkg_")
    db_paths_by_bus = {b: [] for b in BUS_IDS}

    with zipfile.ZipFile(path, "r") as zf:
        with zf.open("manifest.json") as f:
            manifest = json.loads(f.read().decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("format", PACKAGE_FORMAT) != PACKAGE_FORMAT:
            raise ValueError("Not a PJJ user panel package")

        with zf.open("panel/panel.json") as f:
            panel_data = json.loads(f.read().decode("utf-8"))

        for bus in BUS_IDS:
            for item in manifest.get("db_files", {}).get(str(bus), []):
                arc_name = item.get("arc_name")
                if not arc_name:
                    continue
                # BUS별 하위 폴더에 풀어 BUS1/BUS2가 같은 파일명을 써도 서로 덮어쓰지 않게 합니다.
                # basename만 사용하므로 zip 내부 경로로 temp 밖에 쓰는 것(zip-slip)은 막힙니다.
                bus_dir = os.path.join(temp_dir, f"bus{bus}")
                os.makedirs(bus_dir, exist_ok=True)
                out_path = os.path.join(bus_dir, os.path.basename(arc_name))
                with zf.open(arc_name) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                db_paths_by_bus[bus].append(out_path)

    return panel_data, db_paths_by_bus
