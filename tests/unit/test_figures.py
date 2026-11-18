"""The figures of the documentation (U18; RF26; plan, sections 21 and 25): drawn only from the verified files of the site, every one says what it shows in Spanish and, when it comes from the
synthetic invoices, says «sintético»; before the test is sealed it says that the test is evaluated at the close."""

from __future__ import annotations

import json
import shutil

import pytest

from invoiceflow import figures
from invoiceflow.figures import FIGURES, figure_data, write_figures
from invoiceflow.site_export import SiteSetError, read_site

PNG = b"\x89PNG\r\n\x1a\n"


def test_every_figure_is_drawn_from_the_verified_files_as_a_png_with_the_same_bytes_each_time(site_files, figures_written, tmp_path):
    assert [p.name for p in figures_written.paths] == [f"{name}.png" for name in FIGURES] and len(FIGURES) == 7
    for path in figures_written.paths:
        assert path.read_bytes().startswith(PNG) and 5_000 < path.stat().st_size < 200_000, path.name
    again = write_figures(site_files.out, tmp_path / "again")
    assert [p.read_bytes() for p in again] == [p.read_bytes() for p in figures_written.paths] and not list((tmp_path / "again").glob("*.tmp"))


def test_the_numbers_a_figure_plots_are_the_ones_of_the_site_files_and_nothing_else(site_files):
    data = figure_data(site_files.site)
    site = site_files.site
    assert data["operation"]["auto_post"] == [d["auto_post"] for d in site.operation["days"]] and data["operation"]["review"] == [d["review"] for d in site.operation["days"]]
    assert sum(data["review_reasons"]["count"]) == sum(sum(d["by_reason"].values()) for d in site.operation["days"])
    assert data["baselines"]["scope"] == "test" and set(data["baselines"]["baseline"]) >= {"El flujo", "B1: solo el QR en dos vías"} and "B0: todo a revisión" not in data["baselines"]["baseline"], "a baseline that posts nothing has no rate to plot"
    assert data["choice"]["epoch"] == [c["epoch"] for c in site.algorithms["choice"]] and data["choice"]["fit_splits"] == sorted({c["fit_splits"] for c in site.algorithms["choice"]})
    assert data["cord"]["model"] == ["El modelo elegido", "El checkpoint sin ajustar"] or data["cord"]["model"] == ["El checkpoint sin ajustar", "El modelo elegido"]
    assert data["lines"]["rate"] == [site.summary["headline"]["line_exact_known"], site.summary["headline"]["line_exact_new"]]


def test_a_folder_that_is_not_one_export_draws_nothing(site_files, tmp_path):
    folder = tmp_path / "set"
    shutil.copytree(site_files.out, folder)
    (folder / "operation.json").write_bytes(b"{}\n")
    with pytest.raises(SiteSetError):
        write_figures(folder, tmp_path / "figures")
    assert not (tmp_path / "figures").exists() or not list((tmp_path / "figures").iterdir())


def test_a_figure_of_the_synthetic_invoices_says_so_and_before_the_seal_it_says_the_test_is_pending(site_files, monkeypatch):
    titles: list[str] = []
    notes: list[str] = []
    set_title, text = figures.plt.Axes.set_title, figures.plt.Axes.text
    monkeypatch.setattr(figures.plt.Axes, "set_title", lambda self, label, *a, **k: (titles.append(label), set_title(self, label, *a, **k))[1])
    monkeypatch.setattr(figures.plt.Axes, "text", lambda self, x, y, s, *a, **k: (notes.append(s), text(self, x, y, s, *a, **k))[1])
    figures.figure_pngs(figure_data(site_files.site))
    assert len(titles) == 7 and all("(sintético)" in t or "recibos reales" in t for t in titles), titles
    assert not any("se evalúa con el cierre" in n for n in notes)
    pending = json.loads(json.dumps(figure_data(site_files.site)))
    pending["baselines"] = {"scope": "pending", "baseline": [], "cohort": [], "rate": []}
    pending["lines"] = {"cohort": ["conocidos", "diseño nuevo"], "rate": [None, None]}
    pending["cord"] = {"model": [], "ted": [], "scope": "pending"}
    pending["qr"] = {"size": [], "scan": [], "rate": {}}
    notes.clear()
    figures.figure_pngs(pending)
    assert notes.count("La prueba se evalúa con el cierre") == 4


def test_before_the_seal_the_figures_of_the_test_are_pending_and_the_launch_figures_are_there(before_the_seal, tmp_path):
    from invoiceflow.site_export import export_site

    conn, seal, state = before_the_seal
    export_site(conn, tmp_path / "site", state.run, state.view, seal_path=seal, max_total_bytes=204800)
    data = figure_data(read_site(tmp_path / "site"))
    assert data["baselines"]["scope"] == "pending" and data["cord"]["scope"] == "pending" and data["lines"]["rate"] == [None, None] and data["qr"]["size"] == []
    assert data["choice"]["epoch"] == [1, 2] and len(data["operation"]["date"]) == 3
    paths = write_figures(tmp_path / "site", tmp_path / "figures")
    assert len(paths) == 7 and all(p.read_bytes().startswith(PNG) for p in paths)
