import json
import os
import time
import math
import traceback
import appdaemon.plugins.hass.hassapi as hass


class EfficiencyCurveDualMode(hass.Hass):
    def initialize(self):
        # Richtung / Gate (L2)
        self.mode_entity = self.args.get("mode_entity", "sensor.gesamt_kombisensor_laden_entladen")
        self.deadband_w = float(self.args.get("deadband_w", 80))

        # L2 Quellen
        self.plug_entity = self.args.get("plug_entity", "sensor.shellyazplug_b08184ed523c_power")
        self.p1_entity = self.args.get("pack_p1_entity", "sensor.fo4nhn4n2001108_power")
        self.p2_entity = self.args.get("pack_p2_entity", "sensor.fo4nhn4n2400904_power")

        # L3 Quellen (optional) -> schreiben in DIESELBE Datenbasis
        self.enable_l3 = bool(self.args.get("enable_l3", False))
        self.l3_plug_entity = self.args.get("l3_plug_entity", None)
        self.l3_p1_entity = self.args.get("l3_pack_p1_entity", None)
        self.l3_p2_entity = self.args.get("l3_pack_p2_entity", None)
        self.l3_mode_entity = self.args.get("l3_mode_entity", self.mode_entity)

        if self.enable_l3 and not all([self.l3_plug_entity, self.l3_p1_entity, self.l3_p2_entity]):
            self.log("enable_l3=true, aber l3_* entities fehlen -> L3 wird deaktiviert.", level="WARNING")
            self.enable_l3 = False

        # X-Achse: "plug" oder "pack"
        self.x_source = str(self.args.get("x_source", "plug")).lower().strip()

        # Output (keine neuen Entitäten, bleibt wie gehabt)
        base = self.args.get("out_base", "sensor.l2_wirkungsgradkurve")
        self.out_dis = f"{base}_entladen"
        self.out_chg = f"{base}_laden"

        # Buckets / Sampling
        self.bin_w = int(self.args.get("bin_w", 50))
        self.max_w = int(self.args.get("max_w", 2400))
        self.sample_s = int(self.args.get("sample_s", 10))
        self.min_x_w = float(self.args.get("min_x_w", 50))
        self.min_n_plot = int(self.args.get("min_n_plot", 10))

        # Effizienz sanity
        self.y_min = float(self.args.get("y_min", 0))
        self.y_max = float(self.args.get("y_max", 200))

        # Persistenz
        # storage_path MUSS ein Dateipfad sein. Wenn ein Ordner angegeben wird, hängen wir automatisch eine Datei an.
        self.storage_path = self.args.get("storage_path", "wg_curve_all.json")
        if os.path.isdir(self.storage_path):
            self.storage_path = os.path.join(self.storage_path, "wg_curve_all.json")

        self.save_every_s = int(self.args.get("save_every_s", 120))
        self.last_save = 0

        # Data (EINE Datenbasis)
        self.data = self._load()
        self.data.setdefault("discharge", {})
        self.data.setdefault("charge", {})

        # Buckets initialisieren (stabile Keys)
        for start in range(0, self.max_w, self.bin_w):
            end = min(start + self.bin_w, self.max_w)
            key = f"{start}-{end}"
            self.data["discharge"].setdefault(key, {"n": 0, "mean": None, "last_ts": None})
            self.data["charge"].setdefault(key, {"n": 0, "mean": None, "last_ts": None})

        self.log(
            f"DualMode started: mode={self.mode_entity}, deadband={self.deadband_w}W, "
            f"x_source={self.x_source}, bin={self.bin_w}, sample={self.sample_s}s, "
            f"storage={self.storage_path}, L3={'on' if self.enable_l3 else 'off'}"
        )

        self.run_in(self._first_publish, 3)
        self.run_in(self._start_sampling, 5)

    def _start_sampling(self, kwargs):
        self.run_every(self._sample, self.datetime(), self.sample_s)

    def _first_publish(self, kwargs):
        self._publish_safe()

    def _sample(self, kwargs):
        try:
            # L2 sample -> schreibt in self.data
            self._sample_one(
                mode_entity=self.mode_entity,
                plug_entity=self.plug_entity,
                p1_entity=self.p1_entity,
                p2_entity=self.p2_entity,
                source_label="L2"
            )

            # L3 sample -> schreibt in DIESELBE self.data
            if self.enable_l3:
                self._sample_one(
                    mode_entity=self.l3_mode_entity,
                    plug_entity=self.l3_plug_entity,
                    p1_entity=self.l3_p1_entity,
                    p2_entity=self.l3_p2_entity,
                    source_label="L3"
                )

            self._publish_safe()
            self._maybe_save()

        except Exception as e:
            self.log(f"_sample error: {e}\n{traceback.format_exc()}", level="WARNING")

    def _sample_one(self, mode_entity: str, plug_entity: str, p1_entity: str, p2_entity: str, source_label: str):
        mode = self._f(self.get_state(mode_entity))
        if mode is None:
            return

        plug = self._f(self.get_state(plug_entity))
        p1 = self._f(self.get_state(p1_entity))
        p2 = self._f(self.get_state(p2_entity))
        if plug is None or p1 is None or p2 is None:
            return

        pack = p1 + p2

        x = abs(plug) if self.x_source == "plug" else abs(pack)
        if x < self.min_x_w or x > self.max_w:
            return

        if mode > self.deadband_w:
            y = self._eff_discharge(plug, pack)
            if y is None:
                return
            self._update_bin(self.data["discharge"], x, y)

        elif mode < -self.deadband_w:
            y = self._eff_charge(plug, pack)
            if y is None:
                return
            self._update_bin(self.data["charge"], x, y)

        else:
            return

        # Debug (wenn du sehen willst, dass L3 wirklich reinschreibt):
        # self.log(f"{source_label} sample: x={x:.0f}W y={y:.2f}% mode={mode:.0f}", level="DEBUG")

    def _eff_discharge(self, plug, pack):
        denom = abs(pack)
        if denom < 1:
            return None
        y = (abs(plug) / denom) * 100.0
        if y < self.y_min or y > self.y_max:
            return None
        return y

    def _eff_charge(self, plug, pack):
        denom = abs(plug)
        if denom < 1:
            return None
        y = (abs(pack) / denom) * 100.0
        if y < self.y_min or y > self.y_max:
            return None
        return y

    def _update_bin(self, store: dict, x: float, y: float):
        key = self._bucket_key(x)
        if not key or key not in store:
            return

        rec = store[key]
        n = int(rec.get("n", 0))
        mean = rec.get("mean", None)

        if mean is None:
            mean = y
            n = 1
        else:
            n += 1
            mean = float(mean) + (y - float(mean)) / n

        rec["n"] = n
        rec["mean"] = float(mean)
        rec["last_ts"] = int(time.time())

    def _bucket_key(self, x: float):
        start = int(math.floor(x / self.bin_w) * self.bin_w)
        end = min(start + self.bin_w, self.max_w)
        if start < 0 or start >= self.max_w:
            return None
        return f"{start}-{end}"

    def _publish_safe(self):
        try:
            self._publish()
        except Exception as e:
            self.log(f"_publish failed: {e}\n{traceback.format_exc()}", level="WARNING")

    def _publish(self):
        self._publish_one(self.out_dis, self.data["discharge"], "Kennlinie Entladen")
        self._publish_one(self.out_chg, self.data["charge"], "Kennlinie Laden")

    def _publish_one(self, entity_id: str, store: dict, friendly: str):
        pts = []
        for k, v in store.items():
            mean = v.get("mean", None)
            n = int(v.get("n", 0))
            if mean is None or n <= 0:
                continue
            a, b = k.split("-")
            x_mid = (float(a) + float(b)) / 2.0
            pts.append({
                "bin": k,
                "x": round(x_mid, 1),
                "y": round(float(mean), 2),
                "n": n
            })

        pts.sort(key=lambda p: p["x"])

        # state numerisch (sonst meckern Karten)
        best = max([p["y"] for p in pts], default=0.0)
        state_str = f"{best:.2f}"

        # Storage Debug (damit du siehst, WO die Datei wirklich liegt)
        sp_raw = self.storage_path
        sp_abs = os.path.abspath(sp_raw)
        sp_exists = os.path.exists(sp_abs) and os.path.isfile(sp_abs)
        sp_size = os.path.getsize(sp_abs) if sp_exists else 0
        sp_mtime = int(os.path.getmtime(sp_abs)) if sp_exists else None

        self.set_state(entity_id, state=state_str, attributes={
            "friendly_name": friendly,
            "unit_of_measurement": "%",
            "bin_w": self.bin_w,
            "max_w": self.max_w,
            "sample_s": self.sample_s,
            "min_n_plot": self.min_n_plot,
            "deadband_w": self.deadband_w,
            "x_source": self.x_source,
            "curve_points": pts,

            # Debug-Attribute
            "storage_path_raw": sp_raw,
            "storage_path_abs": sp_abs,
            "storage_exists": sp_exists,
            "storage_size_bytes": sp_size,
            "storage_mtime": sp_mtime,
        })

    def _maybe_save(self):
        now = time.time()
        if now - self.last_save < self.save_every_s:
            return
        self._save()
        self.last_save = now

    def _load(self):
        try:
            if os.path.exists(self.storage_path) and os.path.isfile(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            self.log(f"Failed to load {self.storage_path}: {e}\n{traceback.format_exc()}", level="WARNING")
        return {}

    def _save(self):
        try:
            # Verzeichnis sicher anlegen
            d = os.path.dirname(self.storage_path)
            if d:
                os.makedirs(d, exist_ok=True)

            tmp = self.storage_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.storage_path)
        except Exception as e:
            self.log(f"Failed to save {self.storage_path}: {e}\n{traceback.format_exc()}", level="WARNING")

    @staticmethod
    def _f(val):
        try:
            if val in (None, "unknown", "unavailable", ""):
                return None
            return float(val)
        except Exception:
            return None
