(() => {
  "use strict";

  const ARG_ORDER = {
    get_initial_state: [],
    scan_camera: [],
    read_live_tweaks: [],
    import_custom_mod_bytes: ["filename", "base64_data"],
    import_custom_mod_dialog: [],
    remove_custom_mod: ["mod_id"],
    preview_install: ["selection", "known_tweaks", "rescan"],
    start_install: ["selection", "confirmed_permanent", "known_tweaks"],
    cancel_active_job: [],
    list_sd_drives: [],
    preview_sd_sync: ["drive_root", "runtime_selection"],
    start_sd_sync: ["drive_root", "runtime_selection"],
    prepare_tools: [],
    set_mlv2_folder: ["folder_path"],
    pick_mlv2_folder: [],
    run_debugger_check_only: [],
    collect_onboarding: [],
    open_folder: ["which"],
    poll_events: ["after_seq"],
  };

  const state = {
    catalog: { max_active_runtime_mods: 12, runtime_mods: [], pmca_tweaks: [] },
    toolchain: null,
    camera: null,
    receipts: [],
    runtimeSelection: {},
    tweakSelection: {},
    knownTweaks: {},
    sdDrives: [],
    selectedDrive: "",
    eventSeq: 0,
    pollTimer: null,
    stepMap: {},
    pendingInstallSelection: null,
  };

  function webToken() {
    const meta = document.querySelector('meta[name="mm-token"]');
    return meta ? meta.getAttribute("content") : null;
  }

  function waitForBridge() {
    if (webToken() || (window.pywebview && window.pywebview.api)) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const done = () => resolve();
      window.addEventListener("pywebviewready", done, { once: true });
      setTimeout(done, 400);
    });
  }

  async function apiCall(method, params = {}) {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[method] === "function") {
      const keys = ARG_ORDER[method] || [];
      const args = keys.map((k) => params[k]);
      return await window.pywebview.api[method](...args);
    }
    const token = webToken();
    const resp = await fetch(`/api/${method}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-ModManager-Token": token } : {}),
      },
      body: JSON.stringify(params),
    });
    const json = await resp.json();
    if (!resp.ok || !json.ok) {
      throw new Error(json.error || `Request failed (${resp.status})`);
    }
    return json.data;
  }

  function esc(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(message, kind = "ok") {
    const box = document.getElementById("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), 3800);
  }

  function statusPill(status) {
    const map = {
      verified: ["pill-verified", "Verified"],
      usable: ["pill-usable", "Usable"],
      experimental: ["pill-experimental", "Experimental"],
      custom: ["pill-custom", "Custom"],
    };
    const [cls, label] = map[status] || ["pill-muted", status || "Mod"];
    return `<span class="pill ${cls}">${esc(label)}</span>`;
  }

  function initializeSelections(catalog) {
    const nextRuntime = {};
    for (const mod of catalog.runtime_mods || []) {
      if (mod.id in state.runtimeSelection) {
        nextRuntime[mod.id] = Boolean(state.runtimeSelection[mod.id] && mod.available);
      } else {
        nextRuntime[mod.id] = Boolean(mod.default_enabled && mod.available);
      }
    }
    state.runtimeSelection = nextRuntime;

    for (const tw of catalog.pmca_tweaks || []) {
      if (!(tw.id in state.tweakSelection)) {
        state.tweakSelection[tw.id] = null;
      }
    }
  }

  function renderCameraPill() {
    const cam = state.camera || {};
    const pill = document.getElementById("camera-pill");
    const titleEl = document.getElementById("camera-pill-title");
    const subEl = document.getElementById("camera-pill-sub");

    pill.className = `camera-pill state-${cam.state || "offline"}`;
    if (!cam.connected) {
      titleEl.textContent = "Camera Offline";
      subEl.textContent = cam.detail || "Connect Sony camera in Mass Storage mode";
      return;
    }
    const fw = cam.firmware_version ? ` · FW ${cam.firmware_version}` : "";
    titleEl.textContent = `${cam.model_name || "Sony Camera"}${fw}`;
    if (cam.state === "debugger_ready") {
      const stagedCount = (cam.staged_mods || []).length;
      subEl.textContent = `USB Debugger Ready · MLv2: ${cam.mlv2_hook_state || "unknown"} · ${stagedCount} staged`;
    } else if (cam.state === "msc_verified") {
      subEl.textContent = "Mass Storage Ready · 1-Click will bootstrap debugger";
    } else if (cam.state === "msc_blocked") {
      subEl.textContent = `Blocked (${cam.bootstrap_mode})`;
    } else {
      subEl.textContent = "Unsupported model · Use Onboarding Dump";
    }
  }

  function renderMlv2Banner() {
    const banner = document.getElementById("mlv2-banner");
    const mlv2 = state.toolchain?.mlv2;
    if (!mlv2 || mlv2.available) {
      banner.classList.add("hidden");
      return;
    }
    banner.innerHTML = `
      <div>
        <strong>Mod Loader v2 build not found:</strong>
        <span>${esc(mlv2.error || "Download the official mlv2-layout-build-4 release or select a local build folder.")}</span>
      </div>
      <div style="display:flex;gap:8px;flex-shrink:0">
        <button id="btn-banner-download-release" class="btn btn-primary" type="button">Download Official Release</button>
        <button id="btn-banner-pick-mlv2" class="btn btn-secondary" type="button">Select MLv2 Folder...</button>
      </div>
    `;
    banner.classList.remove("hidden");
    document.getElementById("btn-banner-download-release")?.addEventListener("click", handlePrepareTools);
    document.getElementById("btn-banner-pick-mlv2")?.addEventListener("click", handlePickMlv2Folder);
  }

  function renderRuntimeMods() {
    const container = document.getElementById("runtime-mods-list");
    const mods = state.catalog.runtime_mods || [];
    const maxMods = state.catalog.max_active_runtime_mods || 12;

    const activeCount = mods.filter((m) => state.runtimeSelection[m.id] && m.available).length;
    document.getElementById("runtime-counter").textContent = `${activeCount} / ${maxMods} active`;

    container.innerHTML = mods.map((mod) => {
      const isOn = Boolean(state.runtimeSelection[mod.id] && mod.available);
      const smod = mod.smod || {};
      const hookStr = smod.hook_count !== undefined ? `${smod.hook_count} hook(s)` : "N/A";
      const verStr = smod.version ? `SMOD v${smod.version}` : "Unavailable";
      const shaShort = smod.sha256 ? `${smod.sha256.slice(0, 12)}...` : "N/A";
      const sizeStr = smod.file_size ? `${smod.file_size} B` : "N/A";
      const releasePill = mod.release_version === "v5"
        ? `<span class="pill pill-verified">v5</span>`
        : (mod.update_available ? `<span class="pill pill-flash" title="${esc(mod.update_available)}">Update: v5</span>` : "");
      const removeBtn = mod.builtin
        ? ""
        : `<button class="btn btn-danger-sm" data-remove-mod="${esc(mod.id)}" type="button">Remove</button>`;

      return `
        <article class="mod-card ${isOn ? "is-on" : ""} ${mod.available ? "" : "is-unavailable"}" data-mod-card="${esc(mod.id)}">
          <div class="mod-main">
            <div class="mod-info">
              <div class="mod-title-row">
                <span class="mod-title">${esc(mod.title)}</span>
                ${statusPill(mod.status)}
                ${releasePill}
                <span class="pill pill-muted mono">${esc(mod.filename)}</span>
              </div>
              <div class="mod-summary">${esc(mod.available ? mod.summary : mod.unavailable_reason)}</div>
              ${mod.warning ? `<div class="mod-warning">${esc(mod.warning)}</div>` : ""}
            </div>
            <div class="mod-right">
              ${removeBtn}
              <label class="switch" title="${mod.available ? "Toggle ON/OFF" : esc(mod.unavailable_reason)}">
                <input type="checkbox" data-runtime-toggle="${esc(mod.id)}" ${isOn ? "checked" : ""} ${mod.available ? "" : "disabled"}>
                <span class="switch-track"><span class="switch-thumb"></span></span>
                <span class="switch-label">${isOn ? "ON" : "OFF"}</span>
              </label>
              <button class="mod-expand-btn" data-expand-card="${esc(mod.id)}" type="button" aria-label="Details">&#x25BE;</button>
            </div>
          </div>
          <div class="mod-details">
            <div>${esc(mod.details)}</div>
            <div class="meta-grid">
              <div class="meta-item"><strong>Target</strong><span>${esc(mod.target_note)}</span></div>
              <div class="meta-item"><strong>Format</strong><span>${esc(verStr)} · ${esc(hookStr)} · ${esc(sizeStr)}</span></div>
              <div class="meta-item"><strong>SHA-256</strong><span class="mono">${esc(shaShort)}</span></div>
              <div class="meta-item"><strong>Persistence</strong><span>RAM-only (cleared on reboot)</span></div>
            </div>
          </div>
        </article>
      `;
    }).join("");
  }

  function renderPmcaTweaks() {
    const container = document.getElementById("pmca-tweaks-list");
    const tweaks = state.catalog.pmca_tweaks || [];
    container.innerHTML = tweaks.map((tw) => {
      const target = state.tweakSelection[tw.id];
      const known = state.knownTweaks[tw.id];
      const hasKnown = typeof known === "boolean";
      const effectiveOn = target !== null && target !== undefined ? Boolean(target) : Boolean(known);
      const isDirty = target !== null && target !== undefined && (!hasKnown || target !== known);

      let badgeHtml = `<span class="pill pill-muted">Unchanged</span>`;
      if (isDirty) {
        badgeHtml = `<span class="pill pill-flash">Pending: ${target ? "ON" : "OFF"}</span>`;
      } else if (hasKnown) {
        badgeHtml = `<span class="pill ${known ? "pill-verified" : "pill-muted"}">Camera: ${known ? "ON" : "OFF"}</span>`;
      }

      return `
        <article class="mod-card ${effectiveOn ? "is-on" : ""}">
          <div class="mod-main">
            <div class="mod-info">
              <div class="mod-title-row">
                <span class="mod-title">${esc(tw.title)}</span>
                ${badgeHtml}
              </div>
              <div class="mod-summary">${esc(tw.summary)}</div>
            </div>
            <div class="mod-right">
              ${isDirty ? `<button class="btn btn-ghost" style="padding:4px 8px;font-size:11px" data-tweak-clear="${esc(tw.id)}" type="button">Clear</button>` : ""}
              <label class="switch">
                <input type="checkbox" data-tweak-toggle="${esc(tw.id)}" ${effectiveOn ? "checked" : ""}>
                <span class="switch-track"><span class="switch-thumb"></span></span>
                <span class="switch-label">${effectiveOn ? "ON" : "OFF"}</span>
              </label>
            </div>
          </div>
        </article>
      `;
    }).join("");
  }

  function renderActionBar() {
    const runtimeOn = Object.values(state.runtimeSelection).filter(Boolean).length;
    const tweakChanges = Object.entries(state.tweakSelection).filter(([id, val]) => {
      if (val === null || val === undefined) return false;
      return !(id in state.knownTweaks) || state.knownTweaks[id] !== val;
    }).length;

    const titleEl = document.getElementById("action-summary-title");
    const subEl = document.getElementById("action-summary-sub");
    const parts = [];
    parts.push(`${runtimeOn} runtime mod(s) ON`);
    if (tweakChanges > 0) parts.push(`${tweakChanges} PMCA flash tweak(s) pending`);
    titleEl.textContent = parts.join(" · ");

    if (runtimeOn === 0 && tweakChanges === 0) {
      subEl.textContent = "Toggle mods or tweaks ON/OFF, or click 1-Click Install to remove staged mods.";
    } else if (tweakChanges > 0) {
      subEl.textContent = "PMCA flash tweaks will ask for confirmation before writing.";
    } else {
      subEl.textContent = "RAM-only Mod Loader v2 sync (reboot restores stock).";
    }
  }

  function renderToolsPanel() {
    const tc = state.toolchain;
    if (!tc) return;
    const cards = [
      {
        title: "Sony-PMCA-RE",
        ok: tc.pmca?.ready,
        detail: tc.pmca?.ready ? tc.pmca.path : "Not yet cached. Click Download / Prepare Tools.",
      },
      {
        title: "USB / libusb Driver",
        ok: tc.usb?.ready,
        detail: tc.usb?.detail || "Unavailable",
      },
      {
        title: "USB Debugger Payloads",
        ok: tc.debugger_payloads?.ready,
        detail: tc.debugger_payloads?.ready
          ? "a6400_usb_debugd.elf & usb_debug_launcher.so verified (SHA-256)."
          : "Missing or unverified. Click Download / Prepare Tools.",
      },
      {
        title: "Mod Loader v2 Build",
        ok: tc.mlv2?.available,
        detail: tc.mlv2?.available
          ? `${tc.mlv2.is_official_release ? `Official ${tc.mlv2.release_tag} (E0633)` : "Local build"} · ${tc.mlv2.path} (${tc.mlv2.reload_capable ? "hot-reload capable" : "standard core"})`
          : tc.mlv2?.error || "Not found.",
      },
      {
        title: "Arrivederci v5",
        ok: tc.arrivederci_v5?.ready,
        detail: tc.arrivederci_v5?.ready
          ? `Official ${tc.arrivederci_v5.release_tag} SMOD v2 (SHA-256 ${tc.arrivederci_v5.sha256.slice(0, 12)}...)`
          : "Not cached yet (using local copy). Click Download / Prepare Tools.",
      },
    ];

    const needsAttention = cards.some((c) => !c.ok);
    document.getElementById("tools-badge").classList.toggle("hidden", !needsAttention);

    document.getElementById("tools-grid").innerHTML = cards.map((c) => `
      <div class="tool-card">
        <div class="tool-card-top">
          <span>${esc(c.title)}</span>
          <span class="pill ${c.ok ? "pill-verified" : "pill-experimental"}">${c.ok ? "Ready" : "Missing"}</span>
        </div>
        <div class="tool-card-detail">${esc(c.detail)}</div>
      </div>
    `).join("");
  }

  function renderReceipts() {
    const box = document.getElementById("receipts-list");
    const items = state.receipts || [];
    if (!items.length) {
      box.innerHTML = `<div class="muted-meta">No receipts recorded yet.</div>`;
      return;
    }
    box.innerHTML = items.map((r) => `
      <div class="receipt-row">
        <div>
          <strong class="mono">${esc(r.filename)}</strong>
          <div class="muted-meta">${esc(r.operation)} · ${esc(r.camera_model || "SD / Local")} · ${esc(r.timestamp)}</div>
        </div>
        <span class="pill ${r.status === "success" || r.status === "ok" ? "pill-verified" : "pill-experimental"}">${esc(r.status)}</span>
      </div>
    `).join("");
  }

  function buildSelectionPayload() {
    return {
      runtime_mods: { ...state.runtimeSelection },
      pmca_tweaks: { ...state.tweakSelection },
    };
  }

  async function refreshSdDrives() {
    try {
      const res = await apiCall("list_sd_drives");
      state.sdDrives = res.drives || [];
      const select = document.getElementById("sd-drive-select");
      const meta = document.getElementById("sd-drive-meta");

      if (!state.sdDrives.length) {
        select.innerHTML = `<option value="">No removable SD drives detected</option>`;
        state.selectedDrive = "";
        meta.textContent = "Insert a FAT32/exFAT SD card via card reader or MSC.";
        document.getElementById("sd-diff-box").innerHTML = `<div class="muted-meta">No removable drive selected.</div>`;
        document.getElementById("btn-sd-sync").disabled = true;
        return;
      }

      select.innerHTML = state.sdDrives.map((d) => {
        const tag = d.looks_like_sony_sd ? " · Sony SD" : "";
        return `<option value="${esc(d.root)}">${esc(d.root)} (${esc(d.label)}${tag}, ${d.free_mb} MB free)</option>`;
      }).join("");

      if (!state.sdDrives.some((d) => d.root === state.selectedDrive)) {
        const sonyFirst = state.sdDrives.find((d) => d.looks_like_sony_sd) || state.sdDrives[0];
        state.selectedDrive = sonyFirst.root;
      }
      select.value = state.selectedDrive;
      await refreshSdDiff();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function refreshSdDiff() {
    const drive = state.selectedDrive;
    const diffBox = document.getElementById("sd-diff-box");
    const syncBtn = document.getElementById("btn-sd-sync");
    const meta = document.getElementById("sd-drive-meta");
    if (!drive) {
      syncBtn.disabled = true;
      return;
    }
    const driveInfo = state.sdDrives.find((d) => d.root === drive);
    meta.textContent = driveInfo
      ? `${driveInfo.fs_type} · Markers: ${driveInfo.sony_markers.join(", ") || "none"}`
      : "";
    try {
      const { plan } = await apiCall("preview_sd_sync", {
        drive_root: drive,
        runtime_selection: state.runtimeSelection,
      });
      const rows = [
        ...plan.to_add.map((i) => ({ label: i.filename, tag: "Add", cls: "pill-verified" })),
        ...plan.to_update.map((i) => ({ label: i.filename, tag: "Update", cls: "pill-usable" })),
        ...plan.to_keep.map((i) => ({ label: i.filename, tag: "Up to date", cls: "pill-muted" })),
        ...plan.to_remove.map((i) => ({ label: i.filename, tag: "Remove", cls: "pill-danger" })),
      ];
      diffBox.innerHTML = rows.length
        ? rows.map((r) => `<div class="diff-row"><span class="mono">${esc(r.label)}</span><span class="pill ${r.cls}">${esc(r.tag)}</span></div>`).join("")
        : `<div class="muted-meta">No runtime mods enabled and nothing to remove on ${esc(drive)}mods\\.</div>`;
      syncBtn.disabled = !plan.has_changes;
    } catch (err) {
      diffBox.innerHTML = `<div class="muted-meta">${esc(err.message)}</div>`;
      syncBtn.disabled = true;
    }
  }

  function renderAll() {
    renderCameraPill();
    renderMlv2Banner();
    renderRuntimeMods();
    renderPmcaTweaks();
    renderActionBar();
    renderToolsPanel();
    renderReceipts();
  }

  // ---- Modals & Live Progress ----
  function openModal(id) {
    document.getElementById(id).classList.remove("hidden");
  }

  function closeModal(id) {
    document.getElementById(id).classList.add("hidden");
  }

  function resetProgressModal(plan) {
    state.stepMap = {};
    const stepNames = [
      ["verify", "Verify local mods and checksums"],
      ...(plan.tweak_changes?.length ? [["tweaks", "Apply permanent PMCA tweaks & verify read-back"]] : []),
      ...(plan.needs_debugger_bootstrap ? [["bootstrap", "Bootstrap native USB debugger (/setting)"]] : []),
      ...(plan.needs_runtime_sync ? [["mlv2_sync", "Install Mod Loader v2 & sync runtime .mod files"]] : []),
      ["receipt", "Save verification receipt"],
    ];
    for (const [id, text] of stepNames) {
      state.stepMap[id] = { status: "pending", text };
    }
    document.getElementById("power-cycle-callout").classList.add("hidden");
    document.getElementById("progress-result").classList.add("hidden");
    document.getElementById("progress-log").textContent = "";
    document.getElementById("btn-progress-cancel").classList.remove("hidden");
    document.getElementById("btn-progress-done").classList.add("hidden");
    document.getElementById("btn-progress-close").classList.add("hidden");
    document.getElementById("btn-progress-receipt").classList.add("hidden");
    renderProgressSteps();
    openModal("progress-modal");
  }

  function renderProgressSteps() {
    const box = document.getElementById("progress-steps");
    const glyphs = { pending: "•", running: "↻", waiting_power_cycle: "⚡", done: "✓", error: "✕" };
    box.innerHTML = Object.entries(state.stepMap).map(([_id, s]) => {
      const cls = s.status === "waiting_power_cycle" ? "running" : s.status;
      return `
        <div class="step-row ${esc(cls)}">
          <span class="step-badge">${glyphs[s.status] || "•"}</span>
          <span class="step-text">${esc(s.text)}</span>
        </div>
      `;
    }).join("");
  }

  function appendLog(line) {
    const pre = document.getElementById("progress-log");
    pre.textContent += line + "\n";
    pre.scrollTop = pre.scrollHeight;
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function startPolling(onJobDone) {
    stopPolling();
    state.pollTimer = setInterval(async () => {
      try {
        const res = await apiCall("poll_events", { after_seq: state.eventSeq });
        state.eventSeq = res.last_seq;
        for (const ev of res.events || []) {
          if (ev.kind === "progress") {
            appendLog(`[${ev.ts}] ${ev.message}`);
            if (ev.step && state.stepMap[ev.step]) {
              state.stepMap[ev.step] = { status: ev.status || "running", text: ev.message };
              renderProgressSteps();
            }
            if (ev.status === "waiting_power_cycle") {
              document.getElementById("power-cycle-callout").classList.remove("hidden");
            } else if (ev.step === "bootstrap" && ev.status === "done") {
              document.getElementById("power-cycle-callout").classList.add("hidden");
            }
          } else if (ev.kind === "job_done") {
            stopPolling();
            onJobDone(null, ev);
          } else if (ev.kind === "job_error") {
            stopPolling();
            onJobDone(new Error(ev.error || "Operation failed"), ev);
          }
        }
      } catch (err) {
        stopPolling();
        onJobDone(err, null);
      }
    }, 150);
  }

  // ---- Actions ----
  async function handleScanCamera() {
    const btn = document.getElementById("btn-scan-camera");
    btn.disabled = true;
    try {
      const res = await apiCall("scan_camera");
      state.camera = res.camera;
      state.toolchain = res.toolchain;
      renderAll();
      toast(res.camera.connected ? `Detected ${res.camera.model_name}` : "No camera detected", res.camera.connected ? "ok" : "warn");
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  async function handleReadTweaks() {
    const btn = document.getElementById("btn-read-tweaks");
    btn.disabled = true;
    try {
      const res = await apiCall("read_live_tweaks");
      for (const tw of res.tweaks || []) {
        state.knownTweaks[tw.id] = Boolean(tw.enabled);
        state.tweakSelection[tw.id] = Boolean(tw.enabled);
      }
      renderPmcaTweaks();
      renderActionBar();
      toast("Read live PMCA tweak status from camera.", "ok");
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  async function handleOneClickInstall() {
    const btn = document.getElementById("btn-one-click-install");
    btn.disabled = true;
    try {
      const preview = await apiCall("preview_install", {
        selection: buildSelectionPayload(),
        known_tweaks: state.knownTweaks,
        rescan: true,
      });
      state.camera = preview.camera;
      state.toolchain = preview.toolchain;
      renderAll();

      const plan = preview.plan;
      if (plan.blocking_errors?.length) {
        toast(plan.blocking_errors[0], "err");
        return;
      }
      if (!plan.has_changes) {
        toast("Camera already matches your selected toggles.", "ok");
        return;
      }

      state.pendingInstallSelection = { plan, selection: buildSelectionPayload() };
      if (plan.requires_confirmation) {
        const body = document.getElementById("confirm-body");
        body.innerHTML = plan.confirmation_items.map((item) => `
          <div class="confirm-item">
            <div class="confirm-item-title">${esc(item.title)}</div>
            <div class="confirm-item-msg">${esc(item.message)}</div>
          </div>
        `).join("");
        const chk = document.getElementById("confirm-checkbox");
        chk.checked = false;
        document.getElementById("btn-confirm-proceed").disabled = true;
        openModal("confirm-modal");
        return;
      }

      await launchInstallJob(plan, buildSelectionPayload(), false);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  async function launchInstallJob(plan, selection, confirmed) {
    resetProgressModal(plan);
    await apiCall("start_install", {
      selection,
      confirmed_permanent: confirmed,
      known_tweaks: state.knownTweaks,
    });
    startPolling((err, ev) => {
      document.getElementById("power-cycle-callout").classList.add("hidden");
      document.getElementById("btn-progress-cancel").classList.add("hidden");
      document.getElementById("btn-progress-done").classList.remove("hidden");
      document.getElementById("btn-progress-close").classList.remove("hidden");
      const resBox = document.getElementById("progress-result");
      resBox.classList.remove("hidden", "ok", "err", "warn");

      if (err) {
        for (const key of Object.keys(state.stepMap)) {
          if (state.stepMap[key].status === "running" || state.stepMap[key].status === "waiting_power_cycle") {
            state.stepMap[key].status = "error";
          }
        }
        renderProgressSteps();
        resBox.classList.add("err");
        resBox.textContent = err.message;
        appendLog(`[ERROR] ${err.message}`);
        return;
      }

      if (ev.camera) state.camera = ev.camera;
      if (ev.receipts) state.receipts = ev.receipts;
      for (const tw of ev.result?.tweaks || []) {
        state.knownTweaks[tw.id] = Boolean(tw.enabled);
        state.tweakSelection[tw.id] = Boolean(tw.enabled);
      }
      renderAll();

      if (ev.result?.status === "onboarding_collected") {
        if (state.stepMap.bootstrap && state.stepMap.bootstrap.status !== "done") {
          state.stepMap.bootstrap.status = "error";
        }
        renderProgressSteps();
        const reason = ev.result.bootstrap?.reason;
        const bundlePath = ev.result.bootstrap?.bundle_path;
        resBox.classList.add("warn");
        resBox.textContent = (reason ? `${reason} ` : "Camera is unverified. ")
          + `Zero files were modified on camera. Onboarding bundle saved: ${bundlePath}`;
        if (reason) appendLog(`[STOPPED] ${reason}`);
        if (bundlePath) appendLog(`[ONBOARDING] Diagnostic bundle: ${bundlePath}`);
      } else {
        for (const key of Object.keys(state.stepMap)) {
          state.stepMap[key].status = "done";
        }
        renderProgressSteps();
        resBox.classList.add("ok");
        resBox.textContent = ev.result?.message || "Install complete and verified. Open MENU -> Mods on your camera.";
        document.getElementById("btn-progress-receipt").classList.remove("hidden");
      }
    });
  }

  async function handlePrepareTools() {
    const btn = document.getElementById("btn-prepare-tools");
    const bannerBtn = document.getElementById("btn-banner-download-release");
    if (btn) btn.disabled = true;
    if (bannerBtn) bannerBtn.disabled = true;
    try {
      await apiCall("prepare_tools");
      startPolling((err, ev) => {
        if (btn) btn.disabled = false;
        if (bannerBtn) bannerBtn.disabled = false;
        if (err) {
          toast(err.message, "err");
          return;
        }
        if (ev.toolchain) state.toolchain = ev.toolchain;
        if (ev.catalog) {
          state.catalog = ev.catalog;
          initializeSelections(state.catalog);
        }
        renderAll();
        toast("Official MLv2 (mlv2-layout-build-4), Arrivederci v5, PMCA, and debugger binaries ready.", "ok");
      });
    } catch (err) {
      if (btn) btn.disabled = false;
      if (bannerBtn) bannerBtn.disabled = false;
      toast(err.message, "err");
    }
  }

  async function handlePickMlv2Folder() {
    try {
      const res = await apiCall("pick_mlv2_folder");
      if (!res.cancelled) {
        state.toolchain = res.toolchain;
        renderAll();
        toast("Mod Loader v2 build directory verified and saved.", "ok");
      }
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function handleImportCustomBytes(file) {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const b64 = String(reader.result).split(",")[1] || "";
        const res = await apiCall("import_custom_mod_bytes", {
          filename: file.name,
          base64_data: b64,
        });
        state.catalog = res.catalog;
        initializeSelections(state.catalog);
        state.runtimeSelection[res.added.id] = true;
        renderAll();
        toast(`Added custom mod: ${res.added.filename}`, "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    };
    reader.readAsDataURL(file);
  }

  function wireEvents() {
    // Sidebar tabs
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tab = btn.getAttribute("data-tab");
        document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b === btn));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab}`));
        if (tab === "sd") refreshSdDrives();
      });
    });

    document.getElementById("btn-scan-camera").addEventListener("click", handleScanCamera);
    document.getElementById("btn-read-tweaks").addEventListener("click", handleReadTweaks);
    document.getElementById("btn-one-click-install").addEventListener("click", handleOneClickInstall);

    document.getElementById("btn-reset-selection").addEventListener("click", () => {
      for (const m of state.catalog.runtime_mods || []) {
        state.runtimeSelection[m.id] = Boolean(m.default_enabled && m.available);
      }
      for (const t of state.catalog.pmca_tweaks || []) {
        state.tweakSelection[t.id] = t.id in state.knownTweaks ? state.knownTweaks[t.id] : null;
      }
      renderAll();
      toast("Toggles reset to defaults.", "ok");
    });

    // Delegated runtime mod events
    document.getElementById("runtime-mods-list").addEventListener("change", (ev) => {
      const modId = ev.target?.getAttribute("data-runtime-toggle");
      if (!modId) return;
      const checked = Boolean(ev.target.checked);
      state.runtimeSelection[modId] = checked;
      if (checked) {
        const mod = state.catalog.runtime_mods.find((m) => m.id === modId);
        if (mod?.exclusive_group) {
          for (const other of state.catalog.runtime_mods) {
            if (other.id !== modId && other.exclusive_group === mod.exclusive_group && state.runtimeSelection[other.id]) {
              state.runtimeSelection[other.id] = false;
              toast(`Turned OFF ${other.title} (mutually exclusive with ${mod.title}).`, "warn");
            }
          }
        }
      }
      renderRuntimeMods();
      renderActionBar();
    });

    document.getElementById("runtime-mods-list").addEventListener("click", async (ev) => {
      const expandId = ev.target?.closest("[data-expand-card]")?.getAttribute("data-expand-card");
      if (expandId) {
        document.querySelector(`[data-mod-card="${CSS.escape(expandId)}"]`)?.classList.toggle("expanded");
        return;
      }
      const removeId = ev.target?.closest("[data-remove-mod]")?.getAttribute("data-remove-mod");
      if (removeId) {
        try {
          const res = await apiCall("remove_custom_mod", { mod_id: removeId });
          delete state.runtimeSelection[removeId];
          state.catalog = res.catalog;
          initializeSelections(state.catalog);
          renderAll();
          toast("Removed custom mod.", "ok");
        } catch (err) {
          toast(err.message, "err");
        }
      }
    });

    // Delegated PMCA tweak events
    document.getElementById("pmca-tweaks-list").addEventListener("change", (ev) => {
      const tweakId = ev.target?.getAttribute("data-tweak-toggle");
      if (!tweakId) return;
      state.tweakSelection[tweakId] = Boolean(ev.target.checked);
      renderPmcaTweaks();
      renderActionBar();
    });

    document.getElementById("pmca-tweaks-list").addEventListener("click", (ev) => {
      const clearId = ev.target?.getAttribute("data-tweak-clear");
      if (!clearId) return;
      state.tweakSelection[clearId] = clearId in state.knownTweaks ? state.knownTweaks[clearId] : null;
      renderPmcaTweaks();
      renderActionBar();
    });

    // Custom mod dialog & drag/drop
    document.getElementById("btn-add-mod").addEventListener("click", async () => {
      try {
        const res = await apiCall("import_custom_mod_dialog");
        if (!res.cancelled) {
          state.catalog = res.catalog;
          initializeSelections(state.catalog);
          state.runtimeSelection[res.added.id] = true;
          renderAll();
          toast(`Added custom mod: ${res.added.filename}`, "ok");
        }
      } catch (err) {
        toast(err.message, "err");
      }
    });

    const overlay = document.getElementById("drop-overlay");
    let dragDepth = 0;
    window.addEventListener("dragenter", (e) => {
      e.preventDefault();
      dragDepth += 1;
      overlay.classList.remove("hidden");
    });
    window.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) overlay.classList.add("hidden");
    });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      dragDepth = 0;
      overlay.classList.add("hidden");
      const files = Array.from(e.dataTransfer?.files || []);
      for (const file of files) {
        if (!file.name.toLowerCase().endsWith(".mod")) {
          toast(`Ignored ${file.name}: only .mod binaries are supported.`, "warn");
          continue;
        }
        handleImportCustomBytes(file);
      }
    });

    // SD Card Sync
    document.getElementById("btn-refresh-sd").addEventListener("click", refreshSdDrives);
    document.getElementById("sd-drive-select").addEventListener("change", (ev) => {
      state.selectedDrive = ev.target.value;
      refreshSdDiff();
    });
    document.getElementById("btn-sd-sync").addEventListener("click", async () => {
      if (!state.selectedDrive) return;
      const btn = document.getElementById("btn-sd-sync");
      btn.disabled = true;
      try {
        const res = await apiCall("start_sd_sync", {
          drive_root: state.selectedDrive,
          runtime_selection: state.runtimeSelection,
        });
        state.sdDrives = res.drives || [];
        state.receipts = res.receipts || [];
        renderReceipts();
        await refreshSdDiff();
        toast(`Synced ${res.receipt.copied.length} mod(s) to ${res.receipt.mods_dir}`, "ok");
      } catch (err) {
        toast(err.message, "err");
        btn.disabled = false;
      }
    });

    // Tools & Setup
    document.getElementById("btn-prepare-tools").addEventListener("click", handlePrepareTools);

    document.getElementById("btn-pick-mlv2").addEventListener("click", handlePickMlv2Folder);
    document.getElementById("btn-open-receipts").addEventListener("click", () => apiCall("open_folder", { which: "receipts" }));
    document.getElementById("btn-progress-receipt").addEventListener("click", () => apiCall("open_folder", { which: "receipts" }));

    document.getElementById("btn-check-debugger").addEventListener("click", async () => {
      const out = document.getElementById("diag-output");
      out.classList.remove("hidden");
      out.textContent = "Running read-only debugger compatibility check...";
      try {
        const res = await apiCall("run_debugger_check_only");
        out.textContent = JSON.stringify(res, null, 2);
      } catch (err) {
        out.textContent = `Error: ${err.message}`;
      }
    });

    document.getElementById("btn-collect-onboarding").addEventListener("click", async () => {
      const out = document.getElementById("diag-output");
      out.classList.remove("hidden");
      out.textContent = "Collecting read-only onboarding bundle via Senser...";
      try {
        const res = await apiCall("collect_onboarding");
        out.textContent = JSON.stringify(res, null, 2);
        toast("Read-only onboarding bundle exported.", "ok");
      } catch (err) {
        out.textContent = `Error: ${err.message}`;
      }
    });

    // Confirmation & Progress modal controls
    document.getElementById("confirm-checkbox").addEventListener("change", (ev) => {
      document.getElementById("btn-confirm-proceed").disabled = !ev.target.checked;
    });
    document.getElementById("btn-confirm-proceed").addEventListener("click", async () => {
      closeModal("confirm-modal");
      if (!state.pendingInstallSelection) return;
      const { plan, selection } = state.pendingInstallSelection;
      state.pendingInstallSelection = null;
      try {
        await launchInstallJob(plan, selection, true);
      } catch (err) {
        closeModal("progress-modal");
        toast(err.message, "err");
      }
    });
    document.getElementById("btn-progress-cancel").addEventListener("click", async () => {
      await apiCall("cancel_active_job");
      toast("Cancellation requested...", "warn");
    });
    document.querySelectorAll("[data-close]").forEach((btn) => {
      btn.addEventListener("click", () => closeModal(btn.getAttribute("data-close")));
    });
  }

  async function init() {
    await waitForBridge();
    wireEvents();
    try {
      const initData = await apiCall("get_initial_state");
      state.catalog = initData.catalog;
      state.toolchain = initData.toolchain;
      state.camera = initData.camera;
      state.receipts = initData.receipts || [];
      initializeSelections(state.catalog);
      renderAll();
    } catch (err) {
      toast(`Failed to initialize UI: ${err.message}`, "err");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
