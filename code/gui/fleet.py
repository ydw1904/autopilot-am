"""Fleet Management page — similar to https://www.airlines-manager.com/aircraft."""

import asyncio
import os
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nicegui import ui, run
from db import (
    get_db,
    get_fleet_aircraft,
    get_fleet_summary_stats,
    list_hubs_with_routes,
    resolve_skin_ids,
    upsert_fleet,
)
from gui.logbuf import add as log_add
from cdp import CDP, get_am_tab
from planning_page import navigate_to_planning, select_hub, get_aircraft_at_hub


def build(container, on_navigate_livery: Optional[Callable[[int], None]] = None):
    state = {
        "busy": False,
        "view_mode": "grid",  # 'grid' or 'table'
        "rows": [],
        "selected_ids": set(),
        "active_hub_pill": "ALL",
        "skin_id_filter": None,
    }
    refs = {}

    def set_status(msg: str, running: bool = False):
        log_add("fleet", msg)
        if "status_lbl" in refs:
            refs["status_lbl"].set_text(msg)
            refs["status_lbl"].style(
                f'color:{"#9E7600" if running else "var(--text-dim)"};'
            )
        if "spinner" in refs:
            refs["spinner"].set_visibility(running)

    async def reload():
        if state["busy"]:
            return
        # Gather filters
        hubs = refs["sel_hubs"].value if "sel_hubs" in refs else []
        if isinstance(hubs, str) and hubs:
            hubs = [hubs]

        # Pill override
        if state["active_hub_pill"] and state["active_hub_pill"] != "ALL":
            hubs = [state["active_hub_pill"]]

        mquery = refs["inp_model"].value if "inp_model" in refs else ""
        name_q = refs["inp_name"].value if "inp_name" in refs else ""
        util_mode = refs["sel_util"].value if "sel_util" in refs else "all"
        skin_mode = refs["sel_skin"].value if "sel_skin" in refs else "all"
        sort_by = refs["sel_sort"].value if "sel_sort" in refs else "name"

        min_u, max_u = None, None
        if util_mode == "idle":
            min_u, max_u = 0, 0
        elif util_mode == "active":
            min_u, max_u = 0.01, 100.0
        elif util_mode == "full":
            min_u, max_u = 100.0, 100.0
        elif util_mode == "partial":
            min_u, max_u = 0.01, 99.99

        skin_filter = None
        if skin_mode in ("special", "manufacturer"):
            skin_filter = skin_mode

        rows = await run.io_bound(
            get_fleet_aircraft,
            hubs=hubs,
            min_util=min_u,
            max_util=max_u,
            name_query=name_q,
            model_query=mquery,
            skin_filter=skin_filter,
            skin_id=state.get("skin_id_filter"),
            sort_by=sort_by,
        )

        state["rows"] = rows

        # Update stats
        stats = await run.io_bound(get_fleet_summary_stats)
        if "kpi_total" in refs:
            refs["kpi_total"].set_text(f"{stats['total']:,}")
            refs["kpi_active"].set_text(f"{stats['active']:,}")
            refs["kpi_idle"].set_text(f"{stats['idle']:,}")
            refs["kpi_util"].set_text(f"{stats['avg_utilization']}%")
            refs["kpi_special"].set_text(f"{stats['special_skin_count']:,}")

        # Render rows in current view
        _render_view(rows)

        set_status(f"{len(rows):,} aircraft displayed (Total in fleet: {stats['total']:,})")

    def _render_view(rows):
        if state["view_mode"] == "table":
            if "grid_container" in refs:
                refs["grid_container"].set_visibility(False)
            if "table_container" in refs:
                refs["table_container"].set_visibility(True)
            if "table" in refs:
                refs["table"].rows[:] = rows
                refs["table"].update()
        else:
            if "table_container" in refs:
                refs["table_container"].set_visibility(False)
            if "grid_container" in refs:
                refs["grid_container"].set_visibility(True)
                _populate_grid(rows)

    def _populate_grid(rows):
        grid_el = refs.get("grid_cards")
        if not grid_el:
            return
        grid_el.clear()

        limit = 120  # Limit rendered DOM cards for performance
        displayed_rows = rows[:limit]

        with grid_el:
            for ac in displayed_rows:
                aid = ac["aircraft_id"]
                name = ac.get("name") or f"Plane #{aid}"
                model = ac.get("model") or "Unknown"
                hub = ac.get("hub_iata") or "---"
                util = ac.get("utilization") or 0.0
                skin_id = ac.get("skin_id")
                skin_name = ac.get("skin_name")
                skin_rarity = ac.get("skin_rarity")
                is_special_skin = skin_name and "Manufacturer" not in skin_name

                # Card styling
                with ui.element("div").classes("am-aircraft-card"):
                    # Top Row: Checkbox, Name, Hub badge, Util pill
                    with ui.element("div").style(
                        "display:flex; justify-content:space-between; align-items:center; gap:8px;"
                    ):
                        with ui.element("div").style("display:flex; align-items:center; gap:6px; min-width:0;"):
                            cb = ui.checkbox(value=aid in state["selected_ids"])
                            cb.props("dense")
                            cb.on_value_change(
                                lambda e, _id=aid: _toggle_selection(_id, e.value)
                            )
                            ui.label(name).style(
                                "font-weight:700; font-size:13px; color:var(--text-hi); font-family:JetBrains Mono,monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                            )

                        with ui.element("div").style("display:flex; align-items:center; gap:4px; flex-shrink:0;"):
                            ui.label(hub).classes("am-tag am-tag-cyan")

                    # Middle: Livery preview + Model info
                    with ui.element("div").style(
                        "display:flex; gap:10px; align-items:center;"
                    ):
                        # Image
                        with ui.element("div").style(
                            "width:90px; height:50px; background:var(--bg2); border-radius:6px; "
                            "display:flex; align-items:center; justify-content:center; overflow:hidden; border:1px solid var(--panel2); flex-shrink:0;"
                        ):
                            if skin_id:
                                ui.image(f"/api/skin_image/{skin_id}").style(
                                    "max-width:100%; max-height:100%; object-fit:contain;"
                                )
                            else:
                                ui.label("✈").style("font-size:24px; color:var(--text-dim2);")

                        # Model details
                        with ui.element("div").style("flex:1; min-width:0;"):
                            ui.label(model).style(
                                "font-size:12px; font-weight:600; color:var(--text-hi); line-height:1.2;"
                            )
                            specs = []
                            if ac.get("category"):
                                specs.append(f"Cat {ac['category']}")
                            if ac.get("range_km"):
                                specs.append(f"{ac['range_km']:,} km")
                            if ac.get("max_pax"):
                                specs.append(f"{ac['max_pax']} pax")
                            ui.label(" · ".join(specs)).style(
                                "font-size:9px; color:var(--text-dim); font-family:JetBrains Mono,monospace; margin-top:2px;"
                            )

                    # Utilization bar
                    with ui.element("div").style("display:flex; flex-direction:column; gap:2px;"):
                        with ui.element("div").style(
                            "display:flex; justify-content:space-between; font-size:10px; font-family:JetBrains Mono,monospace;"
                        ):
                            util_color = "#1E7E46" if util >= 100 else ("#9E7600" if util > 0 else "#8B877C")
                            status_txt = "FULL (100%)" if util >= 100 else (f"{util:.1f}% ACTIVE" if util > 0 else "IDLE (0%)")
                            ui.label(status_txt).style(f"color:{util_color}; font-weight:600;")
                            if ac.get("wear") is not None:
                                ui.label(f"Wear: {ac['wear']:.1f}%").style("color:var(--text-dim2);")

                        with ui.element("div").style(
                            "width:100%; height:4px; background:var(--bg2); border-radius:2px; overflow:hidden;"
                        ):
                            bar_w = min(100.0, max(0.0, util))
                            ui.element("div").style(
                                f"width:{bar_w}%; height:100%; background:{util_color}; border-radius:2px;"
                            )

                    # Seating Layout & Livery Badge
                    with ui.element("div").style(
                        "display:flex; justify-content:space-between; align-items:center; gap:6px; font-size:10px; border-top:1px solid var(--panel2); padding-top:6px;"
                    ):
                        # Seats
                        if ac.get("seats_eco") is not None:
                            seats_str = f"E:{ac['seats_eco']} B:{ac.get('seats_bus', 0)} F:{ac.get('seats_first', 0)}"
                            ui.label(seats_str).style(
                                "font-family:JetBrains Mono,monospace; font-size:10px; color:var(--text-dim);"
                            )
                        else:
                            ui.label("Config: standard").style("font-size:9px; color:var(--text-dim2);")

                        # Livery badge
                        if is_special_skin:
                            rarity_cls = f"am-rarity-r{skin_rarity}" if skin_rarity is not None else "am-tag-cyan"
                            ui.label(skin_name.split(" - ")[-1][:18]).classes(f"am-tag {rarity_cls}")
                        elif skin_name:
                            ui.label("Mfg Livery").classes("am-tag am-tag-slate")

        if len(rows) > limit:
            with grid_el:
                with ui.element("div").classes("am-panel").style(
                    "grid-column: 1 / -1; text-align:center; padding:12px;"
                ):
                    ui.label(
                        f"Showing first {limit} of {len(rows):,} aircraft in grid view. Switch to Table View for full fast scrolling."
                    ).style("font-size:12px; color:var(--text-dim);")

    def _toggle_selection(aid: int, selected: bool):
        if selected:
            state["selected_ids"].add(aid)
        else:
            state["selected_ids"].discard(aid)
        _update_selection_label()

    def _select_all_visible():
        for r in state["rows"]:
            state["selected_ids"].add(r["aircraft_id"])
        _render_view(state["rows"])
        _update_selection_label()

    def _clear_selection():
        state["selected_ids"].clear()
        _render_view(state["rows"])
        _update_selection_label()

    def _update_selection_label():
        if "sel_count_lbl" in refs:
            cnt = len(state["selected_ids"])
            refs["sel_count_lbl"].set_text(f"{cnt} selected" if cnt else "")

    async def sync_fleet(specific_hub: Optional[str] = None):
        if state["busy"]:
            return
        state["busy"] = True
        hub_text = specific_hub if specific_hub else "all hubs"
        set_status(f"Syncing fleet ({hub_text})… Connecting to AM CDP tab…", running=True)

        tab = get_am_tab()
        if not tab:
            set_status(
                "ERROR: AM tab not found. Launch Chrome with --remote-debugging-port=9222"
            )
            state["busy"] = False
            return

        cdp = CDP(tab["webSocketDebuggerUrl"])
        try:
            await run.io_bound(cdp.connect)
            db = get_db()
            if specific_hub:
                hubs = [specific_hub.upper().strip()]
            else:
                hubs = [r[0] for r in db.execute("SELECT hub_iata FROM player_hubs").fetchall()]

            if not hubs:
                set_status("No hubs found in DB player_hubs table.")
                state["busy"] = False
                return

            set_status("Navigating to /network/planning page…", running=True)
            ok = await run.io_bound(navigate_to_planning, cdp)
            if not ok:
                set_status("Warning: planning page took long to load, continuing…", running=True)

            all_fleet = []
            for idx, hub in enumerate(hubs, 1):
                set_status(f"[{idx}/{len(hubs)}] Scraping hub {hub}…", running=True)
                selected = await run.io_bound(select_hub, cdp, hub)
                if not selected:
                    log_add("fleet", f"hub {hub} select skipped or not found on page")
                    continue

                ac_list = await run.io_bound(get_aircraft_at_hub, cdp, hub)
                for ac in ac_list:
                    ac["hub"] = hub
                all_fleet.extend(ac_list)
                await asyncio.sleep(0.3)

            set_status(f"Resolving liveries and saving {len(all_fleet)} aircraft to DB…", running=True)
            resolved, unresolved = await run.io_bound(resolve_skin_ids, all_fleet)
            await run.io_bound(upsert_fleet, all_fleet)

            set_status(
                f"Sync complete! {len(all_fleet):,} aircraft synced ({resolved} liveries identified)."
            )
            await reload()
        except Exception as ex:
            set_status(f"Sync failed: {ex}")
            log_add("fleet", f"Sync exception: {ex}")
        finally:
            cdp.close()
            state["busy"] = False

    async def bulk_rename():
        sel_ids = list(state["selected_ids"])
        if not sel_ids:
            set_status("Please select aircraft to rename first")
            return

        with ui.dialog() as dialog, ui.card().style("min-width:380px; padding:20px;"):
            ui.label("Bulk Rename Aircraft").classes("text-lg font-bold")
            ui.label(f"Targeting {len(sel_ids)} selected aircraft.").style("color:var(--text-dim); font-size:12px;")
            inp_prefix = ui.input("New Prefix / Name", placeholder="e.g. MPM-C001 or STORAGE").classes("w-full")
            cb_number = ui.checkbox("Add sequential numbering suffix (-001, -002, …)", value=len(sel_ids) > 1)

            with ui.row().style("justify-content:flex-end; width:100%; margin-top:12px;"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Rename",
                    on_click=lambda: dialog.submit((inp_prefix.value.strip(), cb_number.value)),
                ).classes("am-wf am-wf-cyan")

        res = await dialog
        if not res or not res[0]:
            return

        prefix, add_numbers = res
        state["busy"] = True
        set_status(f"Renaming {len(sel_ids)} aircraft to prefix '{prefix}'…", running=True)

        from aircraft_numberer import get_form_token, rename

        tab = get_am_tab()
        if not tab:
            set_status("ERROR: Chrome CDP not open. Please launch Chrome.")
            state["busy"] = False
            return

        cdp = CDP(tab["webSocketDebuggerUrl"])
        try:
            await run.io_bound(cdp.connect)
            ok, fail = 0, 0
            for idx, aid in enumerate(sel_ids, 1):
                new_name = f"{prefix}-{idx:03d}" if add_numbers else prefix
                set_status(f"[{idx}/{len(sel_ids)}] Renaming #{aid} -> {new_name}", running=True)
                tok = await run.io_bound(get_form_token, cdp, aid)
                if tok:
                    status = await run.io_bound(rename, cdp, aid, new_name, tok)
                    if status in (200, 302):
                        ok += 1
                        # Update DB local row
                        db = get_db()
                        db.execute("UPDATE fleet SET name = ? WHERE aircraft_id = ?", (new_name, aid))
                        db.commit()
                    else:
                        fail += 1
                else:
                    fail += 1
                await asyncio.sleep(0.2)
            set_status(f"Rename complete: {ok} succeeded, {fail} failed.")
            await reload()
        except Exception as e:
            set_status(f"Rename failed: {e}")
        finally:
            cdp.close()
            state["busy"] = False

    async def assign_circuit():
        sel_ids = list(state["selected_ids"])
        if not sel_ids:
            set_status("Please select aircraft first")
            return

        with ui.dialog() as dialog, ui.card().style("min-width:380px; padding:20px;"):
            ui.label("Assign to Circuit").classes("text-lg font-bold")
            ui.label(f"Will assign {len(sel_ids)} aircraft to canonical circuit naming format.").style("color:var(--text-dim); font-size:12px;")
            inp_circuit = ui.input("Circuit Code", placeholder="e.g. MPM-C001").classes("w-full")

            with ui.row().style("justify-content:flex-end; width:100%; margin-top:12px;"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Assign", on_click=lambda: dialog.submit(inp_circuit.value.strip().upper())).classes("am-wf am-wf-success")

        circuit_name = await dialog
        if not circuit_name:
            return

        # Perform sequential rename using circuit format
        state["busy"] = True
        set_status(f"Assigning {len(sel_ids)} aircraft to {circuit_name}…", running=True)
        from aircraft_numberer import get_form_token, rename

        tab = get_am_tab()
        cdp = CDP(tab["webSocketDebuggerUrl"])
        try:
            await run.io_bound(cdp.connect)
            ok, fail = 0, 0
            for idx, aid in enumerate(sel_ids, 1):
                new_name = f"{circuit_name}-{idx:03d}"
                tok = await run.io_bound(get_form_token, cdp, aid)
                if tok:
                    status = await run.io_bound(rename, cdp, aid, new_name, tok)
                    if status in (200, 302):
                        ok += 1
                        db = get_db()
                        db.execute("UPDATE fleet SET name = ? WHERE aircraft_id = ?", (new_name, aid))
                        db.commit()
                    else:
                        fail += 1
                else:
                    fail += 1
                await asyncio.sleep(0.2)
            set_status(f"Assignment complete: {ok} ok, {fail} failed.")
            await reload()
        finally:
            cdp.close()
            state["busy"] = False

    def _set_hub_pill(hub_name: str):
        state["active_hub_pill"] = hub_name
        for hname, pill in refs.get("hub_pills", {}).items():
            if hname == hub_name:
                pill.classes(add="active", remove="")
            else:
                pill.classes(remove="active")
        asyncio.ensure_future(reload())

    def _clear_all_filters():
        state["active_hub_pill"] = "ALL"
        state["skin_id_filter"] = None
        for hname, pill in refs.get("hub_pills", {}).items():
            if hname == "ALL":
                pill.classes(add="active", remove="")
            else:
                pill.classes(remove="active")
        if "sel_hubs" in refs:
            refs["sel_hubs"].value = []
        if "inp_model" in refs:
            refs["inp_model"].value = ""
        if "inp_name" in refs:
            refs["inp_name"].value = ""
        if "sel_util" in refs:
            refs["sel_util"].value = "all"
        if "sel_skin" in refs:
            refs["sel_skin"].value = "all"
        asyncio.ensure_future(reload())

    def _switch_view(mode: str):
        state["view_mode"] = mode
        if "btn_grid" in refs and "btn_table" in refs:
            if mode == "grid":
                refs["btn_grid"].classes(add="am-wf-cyan", remove="")
                refs["btn_table"].classes(remove="am-wf-cyan")
            else:
                refs["btn_table"].classes(add="am-wf-cyan", remove="")
                refs["btn_grid"].classes(remove="am-wf-cyan")
        _render_view(state["rows"])

    # ── BUILD UI ─────────────────────────────────────────────────────────────
    with container:
        # Header
        with ui.element("div").classes("am-section-header"):
            with ui.element("div"):
                ui.label("Fleet Management").classes("am-section-title")
                ui.label(
                    "Fleet overview, real-time sync with game, utilization and configurations"
                ).classes("am-section-sub")
            with ui.element("div").classes("am-section-actions"):
                refs["sel_count_lbl"] = ui.label("").style(
                    "font-size:11px; color:var(--accent); font-family:JetBrains Mono,monospace; font-weight:600; margin-right:8px;"
                )
                ui.button("Select All", on_click=_select_all_visible).props("flat dense").classes("am-wf")
                ui.button("Clear Sel", on_click=_clear_selection).props("flat dense").classes("am-wf")
                ui.button("Bulk Rename", on_click=lambda: asyncio.ensure_future(bulk_rename())).props("flat dense").classes("am-wf")
                ui.button("Assign Circuit", on_click=lambda: asyncio.ensure_future(assign_circuit())).props("flat dense").classes("am-wf am-wf-success")
                ui.button("Sync Fleet", on_click=lambda: asyncio.ensure_future(sync_fleet())).props(
                    "flat no-caps dense"
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

            refs["kpi_total"] = _kpi("TOTAL AIRCRAFT", "--", "Owned fleet", "#1D6FB8")
            refs["kpi_active"] = _kpi("ACTIVE FLEET", "--", ">0% utilization", "#1E7E46")
            refs["kpi_idle"] = _kpi("WAREHOUSE (IDLE)", "--", "0% utilization", "#9E7600")
            refs["kpi_util"] = _kpi("AVG UTILIZATION", "--", "Fleet-wide avg", "#7C5CBF")
            refs["kpi_special"] = _kpi("SPECIAL LIVERIES", "--", "Custom/Event skins", "#B07C00")

        # Hub Quick-Filter Pills
        with ui.element("div").style(
            "display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; align-items:center;"
        ):
            ui.label("HUB:").style("font-size:10px; font-weight:700; color:var(--text-dim); font-family:JetBrains Mono,monospace; margin-right:4px;")
            refs["hub_pills"] = {}
            hub_list = [h["hub_iata"] for h in list_hubs_with_routes()]
            
            p_all = ui.element("button").classes("am-pill active")
            with p_all:
                ui.label("ALL")
            p_all.on("click", lambda: _set_hub_pill("ALL"))
            refs["hub_pills"]["ALL"] = p_all

            for h in hub_list[:14]:  # Top hubs
                pill = ui.element("button").classes("am-pill")
                with pill:
                    ui.label(h)
                pill.on("click", lambda _h=h: _set_hub_pill(_h))
                refs["hub_pills"][h] = pill

        # Filter Toolbar
        with ui.element("div").classes("am-panel").style(
            "margin-bottom:14px; display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap;"
        ):
            # Model search
            with ui.element("div").style("display:flex; flex-direction:column; gap:3px; min-width:160px;"):
                ui.label("MODEL / IATA").classes("am-metric-label")
                refs["inp_model"] = ui.input(
                    placeholder="Search model (e.g. 747)",
                    on_change=lambda: asyncio.ensure_future(reload()),
                ).props("dense dark outlined clearable debounce=200").style("width:160px;")

            # Aircraft name search
            with ui.element("div").style("display:flex; flex-direction:column; gap:3px; flex:1; min-width:180px;"):
                ui.label("AIRCRAFT / CIRCUIT NAME").classes("am-metric-label")
                refs["inp_name"] = ui.input(
                    placeholder="e.g. MPM-C001 or SHOP",
                    on_change=lambda: asyncio.ensure_future(reload()),
                ).props("dense dark outlined clearable debounce=200").style("min-width:180px;")

            # Utilization filter
            with ui.element("div").style("display:flex; flex-direction:column; gap:3px; width:140px;"):
                ui.label("UTILIZATION").classes("am-metric-label")
                util_opts = {
                    "all": "All Aircraft",
                    "active": "Active (>0%)",
                    "idle": "Idle Only (0%)",
                    "full": "Full (100%)",
                    "partial": "Partial (<100%)",
                }
                refs["sel_util"] = ui.select(
                    util_opts,
                    value="all",
                    on_change=lambda: asyncio.ensure_future(reload()),
                ).props("dense dark outlined")

            # Livery filter
            with ui.element("div").style("display:flex; flex-direction:column; gap:3px; width:150px;"):
                ui.label("LIVERY TYPE").classes("am-metric-label")
                skin_opts = {
                    "all": "All Liveries",
                    "special": "Special Skins Only",
                    "manufacturer": "Manufacturer Only",
                }
                refs["sel_skin"] = ui.select(
                    skin_opts,
                    value="all",
                    on_change=lambda: asyncio.ensure_future(reload()),
                ).props("dense dark outlined")

            # Sort by
            with ui.element("div").style("display:flex; flex-direction:column; gap:3px; width:150px;"):
                ui.label("SORT BY").classes("am-metric-label")
                sort_opts = {
                    "name": "Name (A-Z)",
                    "util_desc": "Utilization (High-Low)",
                    "util_asc": "Utilization (Low-High)",
                    "hub": "Hub, Model",
                    "model": "Model",
                    "wear_desc": "Wear (High-Low)",
                }
                refs["sel_sort"] = ui.select(
                    sort_opts,
                    value="name",
                    on_change=lambda: asyncio.ensure_future(reload()),
                ).props("dense dark outlined")

            # View Toggle & Reset
            with ui.element("div").style("display:flex; gap:6px; align-items:center; margin-left:auto;"):
                refs["btn_grid"] = ui.button("Grid ⊞", on_click=lambda: _switch_view("grid")).props("flat dense").classes("am-wf am-wf-cyan")
                refs["btn_table"] = ui.button("Table ☰", on_click=lambda: _switch_view("table")).props("flat dense").classes("am-wf")
                ui.button("Clear", on_click=_clear_all_filters).props("flat dense").classes("am-wf am-wf-danger")

        # ── Views Containers ──
        # 1. Grid Container
        grid_container = ui.element("div").style("width:100%; flex:1;")
        refs["grid_container"] = grid_container
        with grid_container:
            refs["grid_cards"] = ui.element("div").classes("am-card-grid")

        # 2. Table Container
        table_container = ui.element("div").style("width:100%; flex:1; display:none;")
        refs["table_container"] = table_container
        with table_container:
            cols = [
                {"name": "name", "label": "AIRCRAFT NAME", "field": "name", "align": "left", "sortable": True},
                {"name": "model", "label": "MODEL", "field": "model", "align": "left", "sortable": True},
                {"name": "hub", "label": "HUB", "field": "hub_iata", "align": "center", "sortable": True},
                {"name": "util", "label": "UTIL %", "field": "utilization", "align": "right", "sortable": True},
                {"name": "seats_eco", "label": "ECO", "field": "seats_eco", "align": "right", "sortable": True},
                {"name": "seats_bus", "label": "BUS", "field": "seats_bus", "align": "right", "sortable": True},
                {"name": "seats_first", "label": "FIR", "field": "seats_first", "align": "right", "sortable": True},
                {"name": "skin", "label": "LIVERY", "field": "skin_name", "align": "left", "sortable": True},
                {"name": "updated", "label": "LAST SYNC", "field": "updated_at", "align": "left", "sortable": True},
            ]
            t = ui.table(columns=cols, rows=[], row_key="aircraft_id", selection="multiple").classes("w-full").style("max-height:550px;")
            t.props("dense virtual-scroll")
            refs["table"] = t

        # Status Bar at Bottom
        with ui.element("div").classes("am-status-bar"):
            refs["spinner"] = ui.element("div").classes("am-spinner")
            refs["spinner"].set_visibility(False)
            refs["status_lbl"] = ui.label("Ready").style("color:var(--text-dim);")

        asyncio.ensure_future(reload())

    def filter_by_skin(skin_id: int):
        state["skin_id_filter"] = skin_id
        asyncio.ensure_future(reload())

    return reload, filter_by_skin
