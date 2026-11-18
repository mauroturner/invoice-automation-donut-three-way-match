"""The plan of deliveries and their manifest (RF06; plan sections 3 and 6; decision D22).

The history of the simulated operation is a launch (``L<last launch day>``: the training and validation partitions and the master), one delivery per day of the test
calendar (``D<day>``: that day's invoices, with their ``as_of`` at 00:00 of the following day, a Saturday's included) and a close (``C<the day after the last test day>``: no
invoices; the one evaluation). ``delivery_plan`` reads only ``[calendar]``. The manifest, ``data/raw/deliveries/manifest.json``, says what was published for each delivery
(its files with their size and SHA-256, the truth file's size and hash, its status ``published`` or ``loaded``); it is written atomically and read strictly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.files import write_atomic
from invoiceflow.synth.invoices import calendar_days

MANIFEST_VERSION = 1
LOADED = "loaded"


@dataclass(frozen=True)
class DeliverySpec:
    delivery_id: str
    kind: str            # launch, daily, close
    day: date
    as_of: datetime      # what the match may know: nothing from this instant on


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min)


def delivery_plan(view: SealedConfig) -> list[DeliverySpec]:
    """The launch, one daily delivery per day of the test calendar and the close, in the order they are made."""
    last_launch = view.calendar.launch_last_day
    plan = [DeliverySpec(f"L{last_launch.isoformat()}", "launch", last_launch, _midnight(last_launch + timedelta(days=1)))]
    plan += [DeliverySpec(f"D{day.isoformat()}", "daily", day, _midnight(day + timedelta(days=1))) for day in calendar_days(view)]
    closing = view.calendar.test_last_day + timedelta(days=1)
    plan.append(DeliverySpec(f"C{closing.isoformat()}", "close", closing, _midnight(closing)))
    return plan


def next_pending(manifest: dict[str, Any], plan: list[DeliverySpec]) -> DeliverySpec | None:
    """The first delivery of the plan that is not loaded (a published one that was never loaded is still the next), or None when all are."""
    done = manifest["deliveries"]
    for spec in plan:
        if done.get(spec.delivery_id, {}).get("status") != LOADED:
            return spec
    return None


def _check(manifest: Any, where: str) -> dict[str, Any]:
    if not (isinstance(manifest, dict) and set(manifest) == {"version", "deliveries"} and manifest["version"] == MANIFEST_VERSION and isinstance(manifest["deliveries"], dict)
            and all(isinstance(entry, dict) for entry in manifest["deliveries"].values())):
        raise ValueError(f"{where} no es un manifiesto de entregas válido (se esperaba {{version: {MANIFEST_VERSION}, deliveries: {{id: {{...}}}}}})")
    return manifest


def read_manifest(path: Path) -> dict[str, Any]:
    """The manifest at `path`; none yet is ``{version, deliveries: {}}``; anything that is not the exact shape is a ``ValueError``."""
    path = Path(path)
    if not path.exists():
        return {"version": MANIFEST_VERSION, "deliveries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"el manifiesto {path.name} no se puede leer como JSON") from error
    return _check(data, f"el manifiesto {path.name}")


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the manifest atomically with sorted keys; one of another shape is refused and the file is left as it was."""
    _check(manifest, "el manifiesto que se iba a escribir")
    write_atomic(Path(path), json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def write_day_truth(path: Path, items: list) -> str:
    """Write the truth of a day's invoices (``truth.write_truth``) for ``deliver``: the flow modules never import the truth module, so the one that makes a delivery asks this one to write it."""
    from invoiceflow.truth import write_truth

    return write_truth(path, items)
