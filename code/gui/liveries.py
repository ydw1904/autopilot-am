"""Livery Collection Management page — browse special liveries, ownership, and aircraft assignments."""

import asyncio
import os
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from db import get_livery_collection
from gui.logbuf import add as log_add


def build(container, on_view_in_fleet: Optional[Callable[[int], None]] = None):
    state = {
        "status_filter": "all",  # 'all', 'owned', 'unowned'
        "rows": [],
    }
    refs = {}

    def set_status(msg: str):
        log_add("liveries", msg)
        if "status_lbl" in refs:
            refs["status_lbl"].set_text(msg)

    async def reload():
        status = state["status_filter"]
        stat_arg = None if status == "all" else status
        mquery = refs["inp_model"].value if "inp_model" in refs else ""
        sq = refs["inp_search"].value if "inp_search" in refs else ""
        sort_by = refs["sel_sort"].value if "sel_sort" in refs else "owned_desc"

        raw_rows = await run.io_bound(
            get_livery_collection,
            include_manufacturer=False,
            status_filter=stat_arg,
            model_query=mquery,
            search_query=sq,
        )

        if sort_by == "owned_desc":
            raw_rows.sort(key=lambda x: (x["owned_count"], x["name"]), reverse=True)
        elif sort_by == "owned_asc":
            raw_rows.sort(key=lambda x: (x["owned_count"], x["name"]))
        else:  # name
            raw_rows.sort(key=lambda x: x["name"] or "")

        state["rows"] = raw_rows

        # Full stats for KPI cards
        all_coll = await run.io_bound(get_livery_collection, include_manufacturer=False)
        total_coll = len(all_coll)
        owned_coll = sum(1 for r in all_coll if r["is_owned"])
        unowned_coll = total_coll - owned_coll
        pct = round((owned_coll / total_coll * 100.0) if total_coll > 0 else 0, 1)

        if "kpi_total" in refs:
            refs["kpi_total"].set_text(str(total_coll))
            refs["kpi_owned"].set_text(str(owned_coll))
            refs["kpi_unowned"].set_text(str(unowned_coll))
            refs["kpi_pct"].set_text(f"{pct}%")
            if "progress_bar" in refs:
                refs["progress_bar"].style(f"width:{pct}%;")

        _populate_grid(raw_rows)
        set_status(
            f"Displaying {len(raw_rows)} liveries ({owned_coll}/{total_coll} collected, {pct}% complete)"
        )

    def _show_all_aircraft_dialog(livery_name: str, planes: list[dict], skin_id: int):
        with ui.dialog() as dialog, ui.card().style("min-width:480px; max-width:640px; padding:20px; max-height:80vh;"):
            with ui.element("div").style("display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;"):
                ui.label(livery_name).classes("text-base font-bold")
                ui.label(f"{len(planes)} aircraft").classes("am-badge-owned")

            with ui.element("div").style("overflow-y:auto; max-height:400px; display:flex; flex-direction:column; gap:6px; margin-bottom:14px;"):
                for p in planes:
                    with ui.element("div").style(
                        "display:flex; justify-content:space-between; align-items:center; padding:6px 10px; background:var(--panel2); border-radius:6px; border:1px solid var(--border);"
                    ):
                        with ui.element("div").style("display:flex; align-items:center; gap:8px;"):
                            ui.label(p["name"]).style("font-weight:700; font-size:12px; font-family:JetBrains Mono,monospace; color:var(--text-hi);")
                            if p.get("hub"):
                                ui.label(p["hub"]).classes("am-tag am-tag-cyan")
                        if p.get("utilization") is not None:
                            util_col = "#1E7E46" if p["utilization"] >= 100 else ("#9E7600" if p["utilization"] > 0 else "#8B877C")
                            ui.label(f"{p['utilization']:.0f}% util").style(f"font-size:11px; color:{util_col}; font-family:JetBrains Mono,monospace;")

            with ui.row().style("justify-content:space-between; width:100%;"):
                ui.button("Close", on_click=dialog.close).props("flat")
                if on_view_in_fleet:
                    ui.button(
                        "View in Fleet Management",
                        on_click=lambda: (dialog.close(), on_view_in_fleet(skin_id)),
                    ).classes("am-wf am-wf-cyan")

    def _populate_grid(rows):
        grid_el = refs.get("livery_grid")
        if not grid_el:
            return
        grid_el.clear()

        if not rows:
            with grid_el:
                with ui.element("div").classes("am-panel").style("grid-column:1 / -1; text-align:center; padding:32px;"):
                    ui.label("No liveries found matching current filters.").style("color:var(--text-dim); font-size:14px;")
            return

        with grid_el:
            for item in rows:
                sid = item["skin_id"]
                name = item.get("name") or f"Skin #{sid}"
                owned_cnt = item.get("owned_count", 0)
                is_owned = item.get("is_owned", False)
                planes = item.get("aircraft", [])
                boosters = item.get("boosters")

                # Extract model from name if format is "<model> - <skin>"
                model_str = ""
                skin_title = name
                if " - " in name:
                    parts = name.split(" - ", 1)
                    model_str = parts[0].strip()
                    skin_title = parts[1].strip()
                elif "_" in name:
                    parts = name.split("_", 1)
                    model_str = parts[0].strip()
                    skin_title = parts[1].replace("_", " ").strip()

                with ui.element("div").classes("am-livery-card"):
                    # Image Box
                    with ui.element("div").classes("am-livery-img-wrap"):
                        ui.image(f"/api/skin_image/{sid}").style("max-width:96%; max-height:96%; object-fit:contain;")

                        # Top-right Ownership Badge
                        with ui.element("div").style("position:absolute; top:8px; right:8px;"):
                            if is_owned:
                                ui.label(f"✔ OWNED ({owned_cnt})").classes("am-badge-owned")
                            else:
                                ui.label("✕ NOT OWNED").classes("am-badge-unowned")

                    # Name & Model
                    with ui.element("div").style("display:flex; flex-direction:column; gap:2px;"):
                        ui.label(skin_title).style(
                            "font-size:14px; font-weight:700; color:var(--text-hi); line-height:1.2;"
                        )
                        if model_str:
                            ui.label(model_str).style(
                                "font-size:11px; font-weight:600; color:var(--cyan); font-family:JetBrains Mono,monospace;"
                            )

                    # Booster / Source info
                    if boosters:
                        with ui.element("div").style("display:flex; align-items:center; gap:4px;"):
                            ui.label("SOURCE:").style("font-size:9px; color:var(--text-dim2); font-family:JetBrains Mono,monospace;")
                            ui.label(boosters[:32]).style("font-size:10px; color:var(--text-dim); font-family:JetBrains Mono,monospace;")

                    # Assigned Aircraft Section
                    with ui.element("div").style(
                        "background:var(--bg2); border:1px solid var(--panel2); border-radius:6px; padding:8px 10px; "
                        "display:flex; flex-direction:column; gap:6px; margin-top:auto;"
                    ):
                        with ui.element("div").style("display:flex; justify-content:space-between; align-items:center;"):
                            ui.label("AIRCRAFT IN FLEET").style(
                                "font-size:9px; font-weight:700; color:var(--text-dim); font-family:JetBrains Mono,monospace; letter-spacing:0.4px;"
                            )
                            if is_owned:
                                ui.label(f"{owned_cnt} active").style(
                                    "font-size:9px; color:var(--green); font-family:JetBrains Mono,monospace; font-weight:600;"
                                )
                            else:
                                ui.label("0 active").style(
                                    "font-size:9px; color:var(--text-dim2); font-family:JetBrains Mono,monospace;"
                                )

                        if is_owned and planes:
                            # Render aircraft names as chips
                            with ui.element("div").style("display:flex; flex-wrap:wrap; gap:4px; align-items:center;"):
                                max_chips = 5
                                for p in planes[:max_chips]:
                                    pname = p.get("name") or "Aircraft"
                                    hub = p.get("hub")
                                    with ui.element("div").classes("am-plane-chip"):
                                        ui.label(pname)
                                        if hub:
                                            ui.label(hub).classes("am-plane-chip-hub")

                                if len(planes) > max_chips:
                                    ui.button(
                                        f"+{len(planes) - max_chips} more…",
                                        on_click=lambda _n=name, _pl=planes, _sid=sid: _show_all_aircraft_dialog(_n, _pl, _sid),
                                    ).props("flat dense no-caps").style(
                                        "font-size:10px; color:var(--cyan); padding:1px 4px; min-height:unset;"
                                    )
                        else:
                            ui.label("Not in fleet — available from drops / market").style(
                                "font-size:10px; color:var(--text-dim2); font-style:italic;"
                            )

                    # Card Action Buttons
                    with ui.element("div").style("display:flex; justify-content:flex-end; gap:6px; align-items:center;"):
                        if is_owned and on_view_in_fleet:
                            ui.button(
                                "View in Fleet",
                                on_click=lambda _sid=sid: on_view_in_fleet(_sid),
                            ).props("flat dense no-caps").classes("am-wf am-wf-cyan")

    def _set_status_filter(status: str):
        state["status_filter"] = status
        for sname, pill in refs.get("status_pills", {}).items():
            if sname == status:
                pill.classes(add="active", remove="")
            else:
                pill.classes(remove="active")
        asyncio.ensure_future(reload())

    def _clear_filters():
        state["status_filter"] = "all"
        for sname, pill in refs.get("status_pills", {}).items():
            if sname == "all":
                pill.classes(add="active", remove="")
            else:
                pill.classes(remove="active")
        if "inp_model" in refs:
            refs["inp_model"].value = ""
        if "inp_search" in refs:
            refs["inp_search"].value = ""
        asyncio.ensure_future(reload())

    # ── BUILD UI ─────────────────────────────────────────────────────────────
    with container:
        # Header
        with ui.element("div").classes("am-section-header"):
            with ui.element("div"):
                ui.label("Livery Collection Management").classes("am-section-title")
                ui.label(
                    "Special & Event liveries collection · Excludes standard manufacturer liveries"
                ).classes("am-section-sub")
            with ui.element("div").classes("am-section-actions"):
                ui.button("Refresh Collection", on_click=lambda: asyncio.ensure_future(reload())).props(
                    "flat dense no-caps"
                ).classes("am-wf am-wf-cyan")

        # KPI Summary Cards
        with ui.element("div").style(
            "display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; margin-bottom:14px;"
        ):
            def _kpi(title, default_val, sub_text, val_color="var(--text-hi)"):
                with ui.element("div").classes("am-stat"):
                    ui.label(title).classes("am-stat-label")
                    lbl = ui.label(default_val).classes("am-stat-value").style(f"color:{val_color};")
                    ui.label(sub_text).classes("am-stat-sub")
                return lbl

            refs["kpi_total"] = _kpi("SPECIAL LIVERIES", "--", "Unique special skins", "#1D6FB8")
            refs["kpi_owned"] = _kpi("COLLECTED / OWNED", "--", "Flying in fleet", "#1E7E46")
            refs["kpi_unowned"] = _kpi("MISSING / CATALOG", "--", "Not owned yet", "#9E7600")
            refs["kpi_pct"] = _kpi("COLLECTION RATE", "--", "Completion %", "#7C5CBF")

        # Collection Progress Bar
        with ui.element("div").style(
            "background:var(--panel2); border:1px solid var(--border); border-radius:8px; padding:10px 14px; margin-bottom:14px;"
        ):
            with ui.element("div").style("display:flex; justify-content:space-between; font-size:11px; margin-bottom:6px; font-family:JetBrains Mono,monospace;"):
                ui.label("COLLECTION COMPLETION PROGRESS").style("color:var(--text-dim); font-weight:600;")
                ui.label("Excluding Standard Manufacturer Liveries").style("color:var(--text-dim2);")

            with ui.element("div").classes("am-progress-track"):
                refs["progress_bar"] = ui.element("div").classes("am-progress-fill").style("width:0%;")

        # Filter Toolbar
        with ui.element("div").classes("am-panel").style(
            "margin-bottom:16px; display:flex; flex-direction:column; gap:12px;"
        ):
            # Row 1: Status Pills
            with ui.element("div").style("display:flex; gap:16px; align-items:center; flex-wrap:wrap;"):
                with ui.element("div").style("display:flex; gap:6px; align-items:center;"):
                    ui.label("STATUS:").style("font-size:10px; font-weight:700; color:var(--text-dim); font-family:JetBrains Mono,monospace;")
                    refs["status_pills"] = {}
                    p_all = ui.element("button").classes("am-pill active")
                    with p_all:
                        ui.label("All Liveries")
                    p_all.on("click", lambda: _set_status_filter("all"))
                    refs["status_pills"]["all"] = p_all

                    p_own = ui.element("button").classes("am-pill")
                    with p_own:
                        ui.label("Owned Only ✔")
                    p_own.on("click", lambda: _set_status_filter("owned"))
                    refs["status_pills"]["owned"] = p_own

                    p_unown = ui.element("button").classes("am-pill")
                    with p_unown:
                        ui.label("Missing / Catalog ✕")
                    p_unown.on("click", lambda: _set_status_filter("unowned"))
                    refs["status_pills"]["unowned"] = p_unown

            # Row 2: Search inputs and Sorting
            with ui.element("div").style("display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap;"):
                # Model search
                with ui.element("div").style("display:flex; flex-direction:column; gap:3px; min-width:160px;"):
                    ui.label("MODEL FILTER").classes("am-metric-label")
                    refs["inp_model"] = ui.input(
                        placeholder="e.g. 737 or A380",
                        on_change=lambda: asyncio.ensure_future(reload()),
                    ).props("dense dark outlined clearable debounce=200").style("width:160px;")

                # Livery Name search
                with ui.element("div").style("display:flex; flex-direction:column; gap:3px; flex:1; min-width:200px;"):
                    ui.label("SEARCH LIVERY OR BOOSTER").classes("am-metric-label")
                    refs["inp_search"] = ui.input(
                        placeholder="Search by name, theme, event…",
                        on_change=lambda: asyncio.ensure_future(reload()),
                    ).props("dense dark outlined clearable debounce=200").style("min-width:200px;")

                # Sorting
                with ui.element("div").style("display:flex; flex-direction:column; gap:3px; width:160px;"):
                    ui.label("SORT ORDER").classes("am-metric-label")
                    sort_opts = {
                        "owned_desc": "Owned (Most Planes)",
                        "owned_asc": "Owned (Least Planes)",
                        "name": "Name (A-Z)",
                    }
                    refs["sel_sort"] = ui.select(
                        sort_opts,
                        value="owned_desc",
                        on_change=lambda: asyncio.ensure_future(reload()),
                    ).props("dense dark outlined")

                ui.button("Clear", on_click=_clear_filters).props("flat dense").classes("am-wf am-wf-danger")

        # Livery Grid
        refs["livery_grid"] = ui.element("div").classes("am-livery-grid")

        # Status Bar
        with ui.element("div").classes("am-status-bar"):
            refs["status_lbl"] = ui.label("Ready").style("color:var(--text-dim);")

        asyncio.ensure_future(reload())

    return reload
