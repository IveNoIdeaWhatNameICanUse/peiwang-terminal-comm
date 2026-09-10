/* [AGENT_CHANGE_BEGIN] 2026-09-09 WebView UI (sync with tk_shell features) */
(function () {
  const $ = (id) => document.getElementById(id);
  const CATS = ["遥信", "遥测", "遥控", "遥调"];
  const COLS = {
    "遥信": [["ioa", "点号"], ["name", "名称"], ["change", "变位次数"], ["soe", "SOE数量"]],
    "遥测": [["ioa", "点号"], ["name", "名称"], ["up", "越上限"], ["down", "越下限"], ["dead", "突变死区"], ["still", "不变告警"]],
    "遥控": [["ioa", "点号"], ["name", "名称"], ["seloff", "预选分"], ["exeoff", "执行分"], ["selon", "预选合"], ["exeon", "执行合"]],
    "遥调": [["ioa", "点号"], ["name", "名称"], ["preset", "预置"], ["exec", "执行(固化)"], ["cancel", "撤销"]],
  };
  const SUMS = {
    "遥信": [["change", "总变位次数"], ["soe", "SOE数量"]],
    "遥测": [["up", "总越上限次数"], ["down", "总越下限次数"], ["dead", "突变死区次数"], ["still", "长期不变告警次数"]],
    "遥控": [["selon", "总预选合次数"], ["seloff", "总预选分次数"], ["exeon", "总执行合次数"], ["exeoff", "总执行分次数"]],
    "遥调": [["preset", "总预置次数"], ["exec", "总执行次数"], ["cancel", "总撤销次数"]],
  };
  let curCat = "遥信";
  let curStatCat = "遥信";
  let curSid = "";

  function appAlert(title, body) {
    $("modalTitle").textContent = title || "提示";
    $("modalBody").textContent = body || "";
    $("modalMask").classList.remove("hidden");
    return new Promise((resolve) => {
      const ok = $("modalOk");
      const handler = () => {
        ok.removeEventListener("click", handler);
        $("modalMask").classList.add("hidden");
        resolve();
      };
      ok.addEventListener("click", handler);
    });
  }

  async function api(method, ...args) {
    if (window.__TK_API__) {
      const fn = window.__TK_API__[method];
      if (!fn) throw new Error("API missing: " + method);
      return await fn(...args);
    }
    if (window.pywebview && window.pywebview.api) {
      return await window.pywebview.api[method](...args);
    }
    throw new Error("后端 API 未就绪");
  }

  function setConnected(on, text) {
    const b = $("connBadge");
    b.className = "badge " + (on ? "on" : "off");
    b.textContent = text || (on ? "已连接" : "未连接");
  }

  function fmtVal(p) {
    const v = p.value;
    if (v === null || v === undefined || v === "") return "";
    const tid = p.type_id || 0;
    if (tid === 1 || tid === 30) return Number(v) === 1 ? "合" : "分";
    if (tid === 3 || tid === 31) return { 0: "不确定", 1: "分", 2: "合", 3: "不确定" }[Number(v)] ?? String(v);
    if (typeof v === "number" && Number.isInteger(v)) return String(v);
    return v;
  }

  async function refreshSessions() {
    const r = await api("list_sessions");
    const sel = $("sessionSel");
    const cur = curSid || r.active_session_id;
    sel.innerHTML = "";
    (r.sessions || []).forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.id;
      const addr = s.protocol === "101" ? `${s.serial_port}@${s.baudrate}` : `${s.remote_ip}:${s.remote_port}`;
      opt.textContent = `${s.name} ${addr}${(r.session_connected || {})[s.id] ? " ●" : ""}`;
      sel.appendChild(opt);
    });
    if (cur) sel.value = cur;
    curSid = sel.value || (r.sessions[0] && r.sessions[0].id);
    await loadSessionForm();
  }

  async function loadSessionForm() {
    if (!curSid) return;
    await api("set_active_session", curSid);
    const p = await api("get_project");
    const s = (p.sessions || []).find((x) => x.id === curSid) || {};
    $("sessionName").value = s.name || "";
    $("remoteIp").value = s.remote_ip || "";
    $("remotePort").value = s.remote_port || 2404;
    $("localPort").value = s.local_port || 0;
    $("ca").value = s.common_address || 1;
    $("oa").value = s.originator || 0;
    $("protocolSel").value = s.protocol || "104";
    updateParamButtons();
    $("variantSel").value = p.protocol_variant || "广西";
    await refreshNics(s.local_ip || "");
    if (s.local_ip) $("localIp").value = s.local_ip;
    renderPoints();
  }

  function collectParams() {
    return {
      name: $("sessionName").value.trim(),
      protocol: $("protocolSel").value,
      remote_ip: $("remoteIp").value.trim(),
      remote_port: Number($("remotePort").value || 2404),
      local_ip: $("localIp").value.trim(),
      local_port: Number($("localPort").value || 0),
      common_address: Number($("ca").value || 1),
      originator: Number($("oa").value || 0),
    };
  }

  function updateParamButtons() {
    const is101 = $("protocolSel").value === "101";
    $("btnParams").disabled = is101;          // 104 参数只给 104 用
    $("btnParams101").disabled = !is101;      // 101 参数只给 101 用
  }

  async function refreshNics(selected) {
    const nics = await api("get_nics");
    const sel = $("localIp");
    const cur = selected || sel.value;
    sel.innerHTML = `<option value="">系统默认（不绑定）</option>`;
    nics.forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n.ip;
      opt.textContent = `${n.ip} (${n.name}${n.is_loopback ? ", loopback" : ""})`;
      sel.appendChild(opt);
    });
    if (cur) sel.value = cur;
  }

  function selectedPointIds() {
    const rows = $("pointBody").querySelectorAll("tr.sel");
    return Array.from(rows).map((r) => Number(r.dataset.ioa));
  }

  function renderPoints() {
    api("get_project").then((p) => {
      const pts = (p.points_by_session || {})[curSid] || p.points || [];
      const tabs = $("catTabs");
      tabs.innerHTML = "";
      CATS.forEach((c) => {
        const b = document.createElement("div");
        b.className = "tab" + (c === curCat ? " on" : "");
        b.textContent = c;
        b.onclick = () => { curCat = c; renderPoints(); };
        tabs.appendChild(b);
      });
      const body = $("pointBody");
      body.innerHTML = "";
      pts.filter((pt) => (pt.category || "") === curCat || (!pt.category && (pt.type_id || 0) === 0)).forEach((pt) => {
        const tr = document.createElement("tr");
        tr.dataset.ioa = pt.ioa;
        tr.onclick = () => tr.classList.toggle("sel");
        tr.innerHTML =
          `<td class="num">${pt.ioa}</td><td>${pt.name || ""}</td><td>${pt.type_id || ""}</td>` +
          `<td>${fmtVal(pt)}</td><td>${pt.quality ?? 0}</td>` +
          `<td contenteditable="true" data-mod="modval">${pt.modval ?? ""}</td>`;
        body.appendChild(tr);
      });
    }).catch(() => {});
  }

  function appendFrame(ev) {
    const el = $("frameLog");
    const line = document.createElement("div");
    line.className = (ev.direction || "").toLowerCase();
    const t = new Date((ev.ts || Date.now() / 1000) * 1000).toLocaleTimeString();
    line.textContent = `[${t}] ${ev.direction || ""} ${ev.note || ""}\n${ev.hex || ""}`;
    el.appendChild(line);
    if ($("autoScroll").checked) el.scrollTop = el.scrollHeight;
  }

  function kindTag(kind) {
    if (kind === "soe") return "soe";
    if ((kind || "").startsWith("ctrl")) return "ctrl";
    if ((kind || "").startsWith("adj")) return "adj";
    if ((kind || "").startsWith("mea")) return "mea";
    if (kind === "sys") return "sys";
    return "cos";
  }

  function renderEvents() {
    api("get_events", curSid, 2000).then((r) => {
      const body = $("eventBody");
      body.innerHTML = "";
      const tagColor = {
        soe: "#15803d", cos: "#b45309", ctrl: "#dc2626",
        adj: "#1d4ed8", mea: "#7c3aed", sys: "#475569",
      };
      (r.events || []).forEach((e) => {
        const tr = document.createElement("tr");
        const tg = kindTag(e.kind);
        tr.innerHTML =
          `<td>${e.ts || ""}</td><td>[${e.ioa}] ${e.name || ""}</td><td>${e.content || ""}</td>` +
          `<td style="color:${tagColor[tg] || "#000"}">${e.kind || ""}</td>`;
        body.appendChild(tr);
      });
    }).catch(() => {});
  }

  function renderStats() {
    api("get_stats", curSid).then((r) => {
      const rows = (r.rows || []).filter((x) => x.cat === curStatCat);
      // tabs
      const tabs = $("statTabs");
      tabs.innerHTML = "";
      CATS.forEach((c) => {
        const b = document.createElement("div");
        b.className = "tab" + (c === curStatCat ? " on" : "");
        b.textContent = c;
        b.onclick = () => { curStatCat = c; renderStats(); };
        tabs.appendChild(b);
      });
      const cols = COLS[curStatCat];
      const head = $("statHead");
      head.innerHTML = "";
      cols.forEach(([k, t]) => {
        const th = document.createElement("th");
        th.textContent = t;
        head.appendChild(th);
      });
      const body = $("statBody");
      body.innerHTML = "";
      const tot = {};
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        cols.forEach(([k]) => {
          const td = document.createElement("td");
          td.textContent = row[k] ?? 0;
          tr.appendChild(td);
        });
        body.appendChild(tr);
        SUMS[curStatCat].forEach(([k]) => { tot[k] = (tot[k] || 0) + Number(row[k] || 0); });
      });
      const sumEl = $("statSum");
      sumEl.innerHTML = "";
      SUMS[curStatCat].forEach(([k, t]) => {
        const s = document.createElement("span");
        s.className = "lbl";
        s.textContent = `${t}：${tot[k] || 0}次`;
        sumEl.appendChild(s);
      });
    }).catch(() => {});
  }

  function switchView(name) {
    $("viewPoints").classList.toggle("hidden", name !== "points");
    $("viewMonitor").classList.toggle("hidden", name !== "monitor");
    $("viewEvents").classList.toggle("hidden", name !== "events");
    $("viewStats").classList.toggle("hidden", name !== "stats");
    $("btnViewPoints").className = name === "points" ? "primary" : "";
    $("btnViewMonitor").className = name === "monitor" ? "primary" : "";
    $("btnViewEvents").className = name === "events" ? "primary" : "";
    $("btnStats").className = name === "stats" ? "primary" : "";
    if (name === "events") renderEvents();
    if (name === "stats") renderStats();
  }

  window.__onNativeEvent = function (ev) {
    if (!ev || !ev.type) return;
    if (ev.type === "connection") {
      setConnected(ev.state === "connected", ev.message || ev.state);
      refreshSessions();
    } else if (ev.type === "frame") {
      appendFrame(ev);
    } else if (ev.type === "points") {
      renderPoints();
    }
  };

  async function boot() {
    await refreshSessions();

    $("sessionSel").onchange = () => {
      curSid = $("sessionSel").value;
      loadSessionForm();
    };
    $("btnNewSession").onclick = async () => {
      const r = await api("create_session");
      if (r.ok) { curSid = r.session.id; await refreshSessions(); }
      else await appAlert("新建失败", r.error || "");
    };
    $("btnDelSession").onclick = async () => {
      const r = await api("delete_session", curSid);
      if (!r.ok) await appAlert("删除失败", r.error || "");
      await refreshSessions();
    };
    $("btnImportConfig").onclick = () => $("importFile").click();
    $("importFile").onchange = async () => {
      const f = $("importFile").files[0];
      if (!f) return;
      const path = f.name;
      try {
        const r = await api("import_config", path);
        if (r.ok) await refreshSessions();
        else await appAlert("导入失败", r.error || "");
      } catch (e) {
        // webview 无法上传原路径：提示
        await appAlert("导入配置", "WebView 模式请将配置文件路径设为当前目录后手动处理，或用 Tk 界面导入。\n" + e);
      }
      $("importFile").value = "";
    };

    $("btnConnect").onclick = async () => {
      const r = await api("connect", collectParams());
      if (!r.ok) {
        await appAlert("连接失败", (r.code ? `[${r.code}] ` : "") + (r.error || ""));
        setConnected(false, "连接失败");
      }
    };
    $("btnDisconnect").onclick = async () => { await api("disconnect", curSid); setConnected(false); };
    $("btnDisconnectAll").onclick = async () => { await api("disconnect_all"); setConnected(false); };
    $("btnRefreshNic").onclick = () => refreshNics();
    $("protocolSel").onchange = () => updateParamButtons();
    $("btnSave").onclick = async () => {
      const r = await api("save_project", await api("get_project"), "");
      if (!r.ok) await appAlert("保存失败", r.error || "");
      else await appAlert("已保存", r.path || "");
    };
    async function openSessionParams(forceProto) {
      const s = (await api("get_project")).sessions.find((x) => x.id === curSid) || {};
      if ((forceProto || s.protocol || "104") === "101") {
        const ports = await api("list_serial_ports");
        const portOpts = (ports || []).map((p) =>
          `<option value="${p.port}"${p.port === (s.serial_port || "") ? " selected" : ""}>${p.port}</option>`).join("");
        const sel = (v, cur) => (String(v) === String(cur) ? " selected" : "");
        const fields = [
          [`串口`, `<select id="p_serial_port">${portOpts || `<option value="${s.serial_port || "COM1"}">${s.serial_port || "COM1"}</option>`}</select>`],
          [`波特率`, `<input id="p_baudrate" type="number" value="${s.baudrate || 9600}" style="width:100px;" />`],
          [`校验`, `<select id="p_serial_parity"><option value="N"${sel("N", s.serial_parity)}>N 无</option><option value="E"${sel("E", s.serial_parity || "E")}>E 偶</option><option value="O"${sel("O", s.serial_parity)}>O 奇</option></select>`],
          [`停止位`, `<select id="p_stopbits"><option value="1"${sel(1, s.stopbits || 1)}>1</option><option value="2"${sel(2, s.stopbits)}>2</option></select>`],
          [`链路地址(>255 自动 2 字节)`, `<input id="p_link_addr" type="number" value="${s.link_addr || 1}" style="width:100px;" />`],
          [`链路地址长度(字节)`, `<select id="p_addr_size"><option value="1"${sel(1, s.addr_size || 2)}>1</option><option value="2"${sel(2, s.addr_size || 2)}>2</option></select>`],
          [`轮询周期(秒)`, `<input id="p_poll_period" type="number" step="0.1" value="${s.poll_period || 1.0}" style="width:100px;" />`],
          [`链路应答超时(秒)`, `<input id="p_link_ack_timeout" type="number" step="1" value="${s.link_ack_timeout || 10}" style="width:100px;" />`],
          [`信息体地址长度(字节)`, `<select id="p_ioa_size_101"><option value="2"${sel(2, s.ioa_size_101 || 2)}>2</option><option value="3"${sel(3, s.ioa_size_101)}>3</option></select>`],
          [`平衡方式`, `<label style="font-weight:normal;"><input id="p_balanced" type="checkbox"${s.balanced ? " checked" : ""} /> 勾选=平衡（不勾=非平衡周期轮询）</label>`],
          [`用户数据帧带 DIR`, `<label style="font-weight:normal;"><input id="p_data_frame_dir" type="checkbox"${s.data_frame_dir ? " checked" : ""} /> 默认不勾（与 KW-2200 现场一致）</label>`],
          [`校验和兼容`, `<label style="font-weight:normal;"><input id="p_cs_compat" type="checkbox"${s.cs_compat === undefined || s.cs_compat ? " checked" : ""} /> 可变帧控制位 bit7 取反（KW-2200 同规则）</label>`],
        ];
        $("modalTitle").textContent = "101 参数设置 - " + (s.name || "");
        $("modalBody").innerHTML = fields
          .map(([label, html]) => `<div>${label}：${html}</div>`).join("<br/>");
        $("modalMask").classList.remove("hidden");
        const ok101 = $("modalOk");
        ok101.textContent = "保存";
        const handler101 = async () => {
          const data = {
            serial_port: $("p_serial_port").value,
            baudrate: Number($("p_baudrate").value || 9600),
            serial_parity: $("p_serial_parity").value,
            stopbits: Number($("p_stopbits").value || 1),
            link_addr: Number($("p_link_addr").value || 1),
            addr_size: Number($("p_addr_size").value || 2),
            poll_period: Number($("p_poll_period").value || 1.0),
            link_ack_timeout: Number($("p_link_ack_timeout").value || 10),
            ioa_size_101: Number($("p_ioa_size_101").value || 2),
            balanced: $("p_balanced").checked,
            data_frame_dir: $("p_data_frame_dir").checked,
            cs_compat: $("p_cs_compat").checked,
          };
          await api("update_session", curSid, data);
          ok101.removeEventListener("click", handler101);
          ok101.textContent = "确定";
          $("modalMask").classList.add("hidden");
          await loadSessionForm();
        };
        ok101.addEventListener("click", handler101);
        return;
      }
      const rows = [
        ["T0 连接超时(秒)", "t0", 30], ["T1 发送/测试超时(秒)", "t1", 15],
        ["T2 确认超时(秒)", "t2", 10], ["T3 空闲测试超时(秒)", "t3", 20],
        ["K 未确认I帧上限", "k", 12], ["W 触发S确认帧数", "w", 8],
        ["总召唤周期(秒,0=禁用)", "gi_period", 600], ["校时周期(分,0=禁用)", "clock_period", 30],
      ];
      const vals = {};
      const lines = rows.map(([label, key, def]) => {
        vals[key] = s[key] ?? def;
        return `${label}:\n  <input id="p_${key}" type="number" value="${vals[key]}" style="width:100px;" />`;
      });
      $("modalTitle").textContent = "104 参数设置 - " + (s.name || "");
      $("modalBody").innerHTML = lines.join("<br/><br/>");
      $("modalMask").classList.remove("hidden");
      const ok = $("modalOk");
      ok.textContent = "保存";
      const handler = async () => {
        const data = {};
        rows.forEach(([, key]) => { data[key] = Number($("p_" + key).value || 0); });
        await api("update_session", curSid, data);
        ok.removeEventListener("click", handler);
        ok.textContent = "确定";
        $("modalMask").classList.add("hidden");
      };
      ok.addEventListener("click", handler);
    }

    $("btnParams").onclick = () => openSessionParams("104");
    $("btnParams101").onclick = () => openSessionParams("101");

    $("btnGI").onclick = async () => {
      const r = await api("general_interrogation");
      if (!r.ok) await appAlert("总召唤失败", r.error || "");
    };
    $("btnClock").onclick = async () => {
      const r = await api("clock_sync");
      if (!r.ok) await appAlert("对时失败", r.error || "");
    };
    $("variantSel").onchange = async () => {
      await api("set_protocol_variant", $("variantSel").value);
    };

    const area = () => Number($("areaNum").value || 1);
    const batch = () => Number($("batchNum").value || 10);
    $("btnReadSel").onclick = async () => {
      const ids = selectedPointIds();
      if (!ids.length) return appAlert("提示", "请先选中定值点");
      const r = await api("read_points", ids, area(), batch());
      if (!r.ok) await appAlert("召唤失败", r.error || "");
    };
    $("btnReadAll").onclick = async () => {
      const r = await api("read_all_setpoints", curSid, area(), batch());
      if (!r.ok) await appAlert("召唤失败", r.error || "");
    };
    $("btnPreset").onclick = async () => {
      const ids = selectedPointIds();
      if (!ids.length) return appAlert("提示", "请先选中定值点并在修改值列填值");
      for (const ioa of ids) {
        const row = $("pointBody").querySelector(`tr[data-ioa="${ioa}"]`);
        const v = row ? row.querySelector("[data-mod]").textContent.trim() : "";
        if (v === "") return appAlert("提示", `请为 IOA ${ioa} 填写修改值`);
        const r = await api("preset_setpoint", ioa, Number(v), true, area());
        if (!r.ok) await appAlert("预置失败", r.error || "");
      }
    };
    $("btnActivate").onclick = async () => {
      const variant = $("variantSel").value;
      if (variant === "国网") {
        const r = await api("fix_setpoint", area());
        if (!r.ok) await appAlert("固化失败", r.error || "");
        return;
      }
      const ids = selectedPointIds();
      if (!ids.length) return appAlert("提示", "请先选中定值点");
      for (const ioa of ids) {
        const row = $("pointBody").querySelector(`tr[data-ioa="${ioa}"]`);
        const v = row ? row.querySelector("[data-mod]").textContent.trim() : "";
        if (v === "") return appAlert("提示", `请为 IOA ${ioa} 填写修改值`);
        const r = await api("preset_setpoint", ioa, Number(v), false, area());
        if (!r.ok) await appAlert("固化失败", r.error || "");
      }
    };
    $("btnUndo").onclick = async () => {
      const variant = $("variantSel").value;
      if (variant === "国网") {
        const r = await api("cancel_setpoint", 0, "", area());
        if (!r.ok) await appAlert("撤销失败", r.error || "");
        return;
      }
      const ids = selectedPointIds();
      if (!ids.length) return appAlert("提示", "请先选中定值点");
      for (const ioa of ids) {
        const r = await api("cancel_setpoint", ioa, "", area());
        if (!r.ok) await appAlert("撤销失败", r.error || "");
      }
    };
    $("btnReadArea").onclick = async () => {
      const r = await api("read_setting_area");
      if (!r.ok) await appAlert("失败", r.error || "");
    };
    $("btnSwitchArea").onclick = async () => {
      const r = await api("switch_setting_area", area());
      if (!r.ok) await appAlert("失败", r.error || "");
    };

    $("btnAddPoint").onclick = async () => {
      const tid = Number($("newType").value);
      const cat = { 1: "遥信", 3: "遥信", 13: "遥测", 45: "遥控", 46: "遥控", 50: "遥调" }[tid] || "";
      const r = await api("upsert_point", {
        ioa: Number($("newIoa").value), type_id: tid,
        name: $("newName").value || "", category: cat,
      }, curSid);
      if (r.ok) renderPoints();
      else await appAlert("添加失败", r.error || "");
    };
    $("btnDelPoint").onclick = async () => {
      const ids = selectedPointIds();
      if (!ids.length) return appAlert("提示", "请先选中要删除的点");
      for (const ioa of ids) await api("remove_point", ioa, curSid);
      renderPoints();
    };
    $("btnImportCsv").onclick = () => appAlert("导入CSV", "WebView 模式需将 CSV 放到可访问路径，请在 Tk 界面使用「导入CSV发码表」。");

    $("btnViewPoints").onclick = () => switchView("points");
    $("btnViewMonitor").onclick = () => switchView("monitor");
    $("btnViewEvents").onclick = () => switchView("events");
    $("btnStats").onclick = () => switchView("stats");
    $("btnClearFrames").onclick = async () => { await api("clear_frames"); $("frameLog").innerHTML = ""; };
    $("btnClearEvents").onclick = async () => { await api("clear_events", curSid); renderEvents(); };
    $("btnRefreshEvents").onclick = renderEvents;
    $("btnRefreshStats").onclick = renderStats;
    $("btnExportFrames").onclick = async () => {
      const text = $("frameLog").textContent;
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `报文-${curSid}.txt`;
      a.click();
    };

    if (window.pywebview) {
      window.addEventListener("pywebviewready", () => refreshSessions());
      setTimeout(refreshSessions, 300);
    } else {
      setTimeout(refreshSessions, 200);
    }
    setInterval(() => {
      if (!$("viewEvents").classList.contains("hidden")) renderEvents();
      if (!$("viewStats").classList.contains("hidden")) renderStats();
    }, 2000);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
/* [AGENT_CHANGE_END] 2026-09-09 WebView UI */
