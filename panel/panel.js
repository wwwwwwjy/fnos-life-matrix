"use strict";

const form = document.querySelector("#settings");
const matrix = document.querySelector("#matrix").getContext("2d");
const message = document.querySelector("#message");
const reseed = document.querySelector("#reseed");
const calibrate = document.querySelector("#calibrate");
const fields = {
  speed: ["速度", 1, 10], brightness: ["亮度", 0, 15], groups: ["初始生命组数", 1, 20],
  networkMin: ["流量下限 MB/s", 0, 100000], networkMax: ["流量上限 MB/s", 1, 100000],
  speedMin: ["最低速度", 1, 10], speedMax: ["最高速度", 1, 10],
  diskMin: ["容量占用下限 %", 0, 99], diskMax: ["容量占用上限 %", 1, 100],
  lifeMin: ["代数上限的最小值", 1, 500], lifeMax: ["代数上限的最大值", 1, 500],
  cpuMin: ["CPU 下限 %", 0, 99], cpuMax: ["CPU 上限 %", 1, 100],
  brightMin: ["最低亮度", 0, 15], brightMax: ["最高亮度", 0, 15],
};
const mappings = [
  ["network", "网络流量 → 速度", ["networkMin", "networkMax", "speedMin", "speedMax"],
    ["interface", "网卡", "interfaces", "留空自动选择"]],
  ["disk", "硬盘容量 → 生命代数上限", ["diskMin", "diskMax", "lifeMin", "lifeMax"],
    ["mount", "存储卷挂载路径", "mounts", "留空自动选择"]],
  ["cpu", "CPU 占用 → 亮度", ["cpuMin", "cpuMax", "brightMin", "brightMax"]],
];
let settings, status, timer, dirty = false, editing = 0, queue = Promise.resolve();

function numberFields(names) {
  return names.map(name => {
    const [label, min, max] = fields[name];
    return `<label>${label}<input type="number" name="${name}" required min="${min}" max="${max}"></label>`;
  }).join("");
}
document.querySelector("#mappings").innerHTML = mappings.map(([key, label, names, source]) => `
  <div class="card">
    <label class="switch"><input type="checkbox" name="${key}">${label}</label>
    <div class="fields">${numberFields(names)}</div>
    ${source ? `<label class="source">${source[1]}<input name="${source[0]}" list="${source[2]}" placeholder="${source[3]}"></label>` : ""}
  </div>`).join("");
document.querySelector("#baseline").innerHTML = numberFields(["speed", "brightness", "groups"]);
document.querySelector("#demo-fields").innerHTML = [
  ["network", "网络 MB/s", 1000], ["disk", "存储卷占用 %", 100], ["cpu", "CPU 占用 %", 100],
].map(([key, label, max]) => `<label>${label}<input type="number" id="demo-${key}" min="0" max="${max}"></label>`).join("");

function say(text, error = false) {
  message.textContent = text;
  message.classList.toggle("error", error);
}
function populate(values) {
  settings = values;
  for (const [key, value] of Object.entries(values)) {
    const input = form.elements.namedItem(key);
    if (typeof value === "boolean") input.checked = value;
    else input.value = value;
  }
}
function readSettings() {
  const values = {};
  for (const [key, value] of Object.entries(settings)) {
    const input = form.elements.namedItem(key);
    if (typeof value === "boolean") values[key] = input.checked;
    else if (typeof value === "number") {
      values[key] = Number(input.value);
      if (input.value === "" || !input.checkValidity()) throw Error(`请检查「${input.labels[0].textContent.trim()}」`);
    } else values[key] = input.value.trim();
  }
  return values;
}
function post(action, payload) {
  const result = queue.then(async () => {
    const response = await fetch(`/api/${action}`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error);
    return data;
  });
  queue = result.catch(() => {});
  return result;
}
form.addEventListener("input", event => {
  if (!settings) return;
  if (!event.target.name) {
    const data = Object.fromEntries(["network", "disk", "cpu"].map(key => [
      key, document.querySelector(`#demo-${key}`).valueAsNumber,
    ]));
    if (Object.values(data).some(value => !Number.isFinite(value))) return;
    data.offline = document.querySelector("#simulate-offline").checked;
    post("demo", data).catch(error => say(error.message, true));
    return;
  }
  dirty = true;
  const change = ++editing;
  clearTimeout(timer);
  timer = setTimeout(async () => {
    try {
      await post("preview", readSettings());
      if (change === editing) say("正在预览，设置尚未保存");
    } catch (error) {
      if (change === editing) say(error.message, true);
    }
  }, 150);
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (!settings) return;
  clearTimeout(timer);
  const change = ++editing;
  try {
    await post("save", readSettings());
    if (change === editing) {
      dirty = false;
      say("设置已保存");
    }
  } catch (error) {
    say(error.message, true);
  }
});
document.querySelector("#discard").addEventListener("click", async () => {
  clearTimeout(timer);
  const change = ++editing;
  try {
    await post("discard", {});
    if (change === editing) {
      dirty = false;
      const response = await fetch("/api/state");
      const data = await response.json();
      if (change === editing) populate(data.settings);
      if (change === editing) say("已还原到上次保存的设置");
    }
  } catch (error) {
    say(error.message, true);
  }
});
reseed.addEventListener("click", () => {
  if (status?.demo) seedDemo();
  else post("reseed", {}).catch(error => say(error.message, true));
});
calibrate.addEventListener("click", () => {
  if (status?.connected) post("calibration", !status.calibrating).catch(error => say(error.message, true));
});

function frontPanelRows(encoded, orientation, reverseModules) {
  const rows = new Uint32Array(8);
  for (let y = 0; y < 8; y++) {
    const logicalRow = Number.parseInt(encoded.slice(y * 8, y * 8 + 8), 16);
    for (let x = 0; x < 32; x++) {
      if (!(logicalRow >>> x & 1)) continue;
      const module = reverseModules ? 3 - (x >>> 3) : x >>> 3;
      let row = y, bit = x % 8;
      if (orientation & 1) [row, bit] = [bit, row];
      if (orientation & 2) row = 7 - row;
      if (orientation & 4) bit = 7 - bit;
      // On this front panel the leftmost lamp of each module is register bit 7.
      rows[row] |= 1 << (module * 8 + 7 - bit);
    }
  }
  return rows;
}

function draw(frame) {
  const rows = frame && !frame.dark ? frontPanelRows(
    frame.rows, status?.settings?.orientation ?? 5, status?.settings?.reverse ?? false,
  ) : new Uint32Array(8);
  const face = matrix.createLinearGradient(0, 0, 0, matrix.canvas.height);
  face.addColorStop(0, "#f4f3ec");
  face.addColorStop(1, "#deddd4");
  matrix.fillStyle = face;
  matrix.fillRect(0, 0, matrix.canvas.width, matrix.canvas.height);
  const brightness = frame?.brightness ?? 0;
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 32; x++) {
      const lit = (rows[y] >>> x) & 1;
      const left = 28 + x * 33 + (x >>> 3) * 8;
      const top = 30 + y * 33;
      matrix.beginPath();
      matrix.roundRect(left, top, 25, 25, 5);
      matrix.fillStyle = lit ? `hsl(84, 100%, ${48 + brightness}%)` : "#706e63";
      if (lit) {
        matrix.shadowColor = "#83ec00";
        matrix.shadowBlur = 5 + brightness;
      }
      matrix.fill();
      matrix.shadowBlur = 0;
      if (!lit) {
        const recess = matrix.createLinearGradient(left, top, left + 14, top + 25);
        recess.addColorStop(0, "#5f5e51");
        recess.addColorStop(1, "#aaa797");
        matrix.beginPath();
        matrix.roundRect(left + 2, top + 2, 22, 22, 4);
        matrix.fillStyle = recess;
        matrix.fill();
      }
    }
  }
}

let demoGrid, demoHistory, demoGeneration, lastDemo = 0;
function seedDemo() {
  demoGrid = new Uint32Array(8);
  demoHistory = [];
  demoGeneration = 0;
  for (let i = 0; i < (status?.effective?.groups ?? 5); i++) {
    const x = Math.floor(Math.random() * 32), y = Math.floor(Math.random() * 8);
    demoGrid[y] |= (1 << x) | (x > 0 ? 1 << (x - 1) : 0) | (x < 31 ? 1 << (x + 1) : 0);
    if (y > 0) demoGrid[y - 1] |= 1 << x;
    if (y < 7) demoGrid[y + 1] |= 1 << x;
  }
}
const encode = grid => Array.from(grid, row => row.toString(16).padStart(8, "0")).join("");
function renderDemo(now) {
  if (!status?.demo || !status.effective) return;
  const {speed, brightness, life, dark} = status.effective;
  if (!demoGrid) seedDemo();
  if (now - lastDemo >= 50 * (11 - speed)) {
    lastDemo = now;
    const next = new Uint32Array(8);
    for (let y = 0; y < 8; y++) {
      for (let x = 0; x < 32; x++) {
        let count = 0;
        for (let row = Math.max(0, y - 1); row <= Math.min(7, y + 1); row++) {
          for (let col = Math.max(0, x - 1); col <= Math.min(31, x + 1); col++) {
            if (row !== y || col !== x) count += (demoGrid[row] >>> col) & 1;
          }
        }
        if (count === 3 || (count === 2 && ((demoGrid[y] >>> x) & 1))) next[y] |= 1 << x;
      }
    }
    const signature = encode(next);
    if (!next.some(Boolean) || signature === encode(demoGrid) || demoHistory.includes(signature) ||
        (life && demoGeneration >= life)) seedDemo();
    else {
      demoGrid = next;
      demoGeneration++;
      demoHistory = [signature, ...demoHistory.slice(0, 7)];
    }
  }
  showFrame({rows: encode(demoGrid), generation: demoGeneration, speed, brightness, dark}, life);
}
function showFrame(frame, life) {
  draw(frame);
  document.querySelector("#readout").textContent = !frame ? "接上灯阵后显示 ESP32 的实际画面" :
    frame.dark ? "灯阵已熄灭" : status?.calibrating ? "固定校准图案" :
      `第 ${frame.generation} 代 · 速度 ${frame.speed} / 10 · 亮度 ${frame.brightness} / 15${life ? ` · 代数上限 ${life}` : ""}`;
}

function sources(id, values) {
  const list = document.querySelector(`#${id}`);
  if (JSON.stringify(values) === list.dataset.values) return;
  list.dataset.values = JSON.stringify(values);
  list.replaceChildren(...values.map(value => {
    const option = document.createElement("option");
    option.value = value;
    return option;
  }));
}
async function poll() {
  const finishedBefore = editing;
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw Error("面板通讯失败");
    status = await response.json();
    if (!settings || (!dirty && finishedBefore === editing && JSON.stringify(settings) !== JSON.stringify(status.settings))) {
      populate(status.settings);
    }
    const connection = document.querySelector("#connection");
    connection.textContent = status.demo ? "模拟模式 · 使用测试指标" :
      status.connected ? `ESP32 已连接${status.boardSaved ? " · 独立运行参数已写入灯板" : ""}` :
        status.error || "等待 ESP32 · 请连接 USB 数据线并检查串口";
    reseed.disabled = !status.connected && !status.demo;
    calibrate.disabled = !status.connected;
    calibrate.textContent = status.calibrating ? "结束校准" : "显示校准图案";
    document.querySelector("#calibration-help").hidden = !status.calibrating;
    const demo = document.querySelector("#demo");
    if (status.demo && demo.hidden) {
      demo.hidden = false;
      for (const key of ["cpu", "disk", "network"]) document.querySelector(`#demo-${key}`).value = status.demoValues[key];
      document.querySelector("#simulate-offline").checked = status.demoValues.offline;
    }
    const metrics = status.metrics;
    if (metrics) {
      for (const [id, value, unit] of [["net", metrics.network, " MB/s"], ["cpu", metrics.cpu, "%"], ["disk", metrics.disk, "%"]]) {
        document.querySelector(`#${id}`).textContent = value === null ? "—" : value + unit;
      }
      sources("interfaces", metrics.interfaces);
      sources("mounts", metrics.mounts);
      form.elements.interface.placeholder = metrics.interface || "留空自动选择";
      form.elements.mount.placeholder = metrics.mount || "留空自动选择";
    }
    if (!status.demo) showFrame(status.frame, status.effective?.life);
  } catch {
    document.querySelector("#connection").textContent = "控制面板已断开，正在重试";
    reseed.disabled = true;
    calibrate.disabled = true;
    status = null;
    showFrame(null);
  } finally {
    setTimeout(poll, 45);
  }
}
draw(null);
poll();
setInterval(() => renderDemo(performance.now()), 50);
