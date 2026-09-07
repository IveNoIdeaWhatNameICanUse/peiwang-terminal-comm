/* [AGENT_CHANGE_BEGIN] 2026-09-07 104-MVP前端逻辑 */
(function () {
  const $ = (id) => document.getElementById(id);

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
    if (!window.pywebview || !window.pywebview.api) {
      // tk 桥：通过全局 __TK_API__
      if (window.__TK_API__) {
        const fn = window.__TK_API__[method];
        if (!fn) throw new Error("API 不存在: " + method);
        return await fn(...args);
      }
      throw new Error("后端 API 未就绪");
    }
    return await window.pywebview.api[method](...args);
  }

  function setConnected(on, text) {
    const b = $("connBadge");
    b.className = "badge " + (on ? "on" : "off");
    b.textContent = text || (on ? "已连接" : "未连接");
  }

  function appendFrame(ev) {
    const el = $("frameLog");
    const line = document.createElement("div");
    line.className = (ev.direction || "").toLowerCase();
    const t = new Date((ev.ts || Date.now() / 1000) * 1000).toLocaleTimeString();
    line.textContent = `[${t}] ${ev.direction || ""} ${ev.note || ""}\n${ev.hex || ""}`;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function renderPoints(project) {
    const body = $("pointBody");
    body.innerHTML = "";
    (project.points || []).forEach((p) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${p.ioa}</td><td>${p.name || ""}</td><td>${p.type_id}</td><td>${p.value ?? ""}</td><td>${p.quality ?? 0}</td>
        <td><button data-ioa="${p.ioa}" class="del">删</button></td>`;
      body.appendChild(tr);
    });
    body.querySelectorAll("button.del").forEach((btn) => {
      btn.onclick = async () => {
        const r = await api("remove_point", Number(btn.dataset.ioa));
        if (r.ok) renderPoints(r.project);
      };
    });
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

  async function loadProject() {
    const p = await api("get_project");
    $("remoteIp").value = p.remote_ip || "127.0.0.1";
    $("remotePort").value = p.remote_port || 2404;
    $("localPort").value = p.local_port || "";
    $("ca").value = p.common_address || 1;
    $("oa").value = p.originator || 0;
    await refreshNics(p.local_ip || "");
    if (p.local_ip) $("localIp").value = p.local_ip;
    renderPoints(p);
  }

  function collectParams() {
    const lp = $("localPort").value;
    return {
      remote_ip: $("remoteIp").value.trim(),
      remote_port: Number($("remotePort").value || 2404),
      local_ip: $("localIp").value.trim(),
      local_port: lp === "" ? 0 : Number(lp),
      common_address: Number($("ca").value || 1),
      originator: Number($("oa").value || 0),
    };
  }

  window.__onNativeEvent = function (ev) {
    if (!ev || !ev.type) return;
    if (ev.type === "connection") {
      setConnected(ev.state === "connected", ev.message || ev.state);
    } else if (ev.type === "frame") {
      appendFrame(ev);
    } else if (ev.type === "points") {
      api("get_project").then(renderPoints);
    }
  };

  async function boot() {
    $("btnRefreshNic").onclick = () => refreshNics();
    $("btnConnect").onclick = async () => {
      const r = await api("connect", collectParams());
      if (!r.ok) {
        await appAlert("连接失败", (r.code ? `[${r.code}] ` : "") + (r.error || "未知错误"));
        setConnected(false, "连接失败");
      }
    };
    $("btnDisconnect").onclick = async () => {
      await api("disconnect");
      setConnected(false);
    };
    $("btnGI").onclick = async () => {
      const r = await api("general_interrogation");
      if (!r.ok) await appAlert("总召唤失败", r.error || "");
    };
    $("btnClock").onclick = async () => {
      const r = await api("clock_sync");
      if (!r.ok) await appAlert("对时失败", r.error || "");
    };
    $("btnCmd").onclick = async () => sendCmd(true);
    $("btnExecute").onclick = async () => sendCmd(false);
    $("btnAddPoint").onclick = async () => {
      const typeId = Number($("newType").value);
      const cat = typeId === 1 ? "遥信" : typeId === 13 ? "遥测" : typeId === 45 ? "遥控" : "遥调";
      const r = await api("upsert_point", {
        ioa: Number($("newIoa").value),
        type_id: typeId,
        name: $("newName").value || "",
        category: cat,
      });
      if (r.ok) renderPoints(r.project);
    };
    $("btnSave").onclick = async () => {
      const data = Object.assign(await api("get_project"), collectParams());
      const r = await api("save_project", data, "");
      if (!r.ok) await appAlert("保存失败", r.error || "");
      else await appAlert("已保存", r.path || "");
    };
    $("btnClearFrames").onclick = async () => {
      await api("clear_frames");
      $("frameLog").innerHTML = "";
    };

    async function sendCmd(useSelectFlag) {
      const kind = $("cmdKind").value;
      const ioa = Number($("cmdIoa").value);
      const select = useSelectFlag ? $("cmdSelect").checked : false;
      const raw = $("cmdValue").value;
      let r;
      if (kind === "sc") {
        r = await api("single_command", ioa, Number(raw) === 1, select);
      } else if (kind === "dc") {
        r = await api("double_command", ioa, Number(raw), select);
      } else if (kind === "se_nc") {
        r = await api("setpoint_float", ioa, Number(raw), select);
      } else {
        r = await api("setpoint_normalized", ioa, Number(raw), select);
      }
      if (!r.ok) await appAlert("命令失败", (r.code ? `[${r.code}] ` : "") + (r.error || ""));
    }

    // 等待 pywebview ready
    if (window.pywebview) {
      await loadProject();
    } else {
      window.addEventListener("pywebviewready", loadProject);
      // tk 模式直接加载
      setTimeout(loadProject, 200);
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
/* [AGENT_CHANGE_END] 2026-09-07 104-MVP前端逻辑 */
