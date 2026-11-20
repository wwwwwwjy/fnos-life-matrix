// Included inside the firmware's private namespace.
StaticJsonDocument<2048> panelSaved, panelPreview;
StaticJsonDocument<2048> nasMetrics;
bool panelRequested = false;
bool hasNasData = false;
bool webReseed = false;
uint32_t lastNasData = 0;
unsigned long requestId = 0;
const char panelDefaults[] = R"({
  "power":true,"linked":false,"network":true,"disk":true,"cpu":true,"offline":true,
  "speed":3,"groups":5,"brightness":5,"speedMin":2,"speedMax":9,
  "networkMin":0,"networkMax":100,"lifeMin":8,"lifeMax":80,"diskMin":0,"diskMax":100,
  "brightMin":2,"brightMax":12,"cpuMin":0,"cpuMax":100,
  "orientation":5,"reverse":false,"interface":"","mount":""
})";

bool panelSettingsValid(JsonObjectConst candidate) {
    if (candidate.size() != panelSaved.size()) return false;
    for (const char* key : {"power","linked","network","disk","cpu","offline","reverse"}) {
        if (!candidate[key].is<bool>()) return false;
    }
    struct Limit { const char* key; int minimum, maximum; };
    const Limit limits[] = {
        {"speed",1,10},{"groups",1,20},{"brightness",0,15},{"speedMin",1,10},{"speedMax",1,10},
        {"networkMin",0,100000},{"networkMax",0,100000},{"lifeMin",1,500},{"lifeMax",1,500},
        {"diskMin",0,100},{"diskMax",0,100},{"brightMin",0,15},{"brightMax",0,15},
        {"cpuMin",0,100},{"cpuMax",0,100},{"orientation",0,7},
    };
    for (const auto& limit : limits) {
        const auto value = candidate[limit.key];
        if (!value.is<int>() || value.as<int>() < limit.minimum || value.as<int>() > limit.maximum) return false;
    }
    return candidate["interface"].is<const char*>() && candidate["mount"].is<const char*>() &&
           strlen(candidate["interface"]) <= 64 && strlen(candidate["mount"]) <= 160 &&
           candidate["networkMin"].as<int>() < candidate["networkMax"].as<int>() &&
           candidate["diskMin"].as<int>() < candidate["diskMax"].as<int>() &&
           candidate["cpuMin"].as<int>() < candidate["cpuMax"].as<int>() &&
           candidate["speedMin"].as<int>() <= candidate["speedMax"].as<int>() &&
           candidate["lifeMin"].as<int>() <= candidate["lifeMax"].as<int>() &&
           candidate["brightMin"].as<int>() <= candidate["brightMax"].as<int>();
}

void panelReply(int code, const char* body) {
    const size_t length = strlen(body);
    Serial.printf("A %lu %d %u\n", requestId, code, unsigned(length));
    Serial.write(reinterpret_cast<const uint8_t*>(body), length);
}

bool nasOnline() {
    return hasNasData && millis() - lastNasData < telemetryTimeout;
}

void serveState() {
    static StaticJsonDocument<5500> state;
    state.clear();
    state["settings"].set(panelPreview);
    state["saved"].set(panelSaved);
    state["demo"] = false;
    state["connected"] = true;
    state["calibrating"] = displayedCalibration;
    state["boardSaved"] = panelPreview == panelSaved;
    if (nasOnline()) state["metrics"].set(nasMetrics);
    else {
        auto metrics = state.createNestedObject("metrics");
        metrics["network"] = nullptr;
        metrics["disk"] = nullptr;
        metrics["cpu"] = nullptr;
        metrics.createNestedArray("interfaces");
        metrics.createNestedArray("mounts");
    }
    auto frame = state.createNestedObject("frame");
    frame["generation"] = displayedGeneration;
    frame["speed"] = displayedSpeed;
    frame["brightness"] = max(0, shownBrightness);
    frame["dark"] = shownDark;
    frame["rows"] = webRows.length() == 64 ? webRows : String("0000000000000000000000000000000000000000000000000000000000000000");
    state.createNestedObject("effective")["life"] = liveLifetime;
    String body;
    serializeJson(state, body);
    panelReply(200, body.c_str());
}

void writePanel(const String& action, const char* body) {
    StaticJsonDocument<2048> request;
    if (deserializeJson(request, body)) {
        panelReply(400, "{\"error\":\"数据格式有误\"}");
        return;
    }
    if (action == "reseed") webReseed = true;
    else if (action == "discard") panelPreview.set(panelSaved);
    else if (action == "calibration" && request.is<bool>()) calibrating = request.as<bool>();
    else if (action == "preview" || action == "save") {
        if (!panelSettingsValid(request.as<JsonObjectConst>())) {
            panelReply(400, "{\"error\":\"设置范围不正确，请检查上下限\"}");
            return;
        }
        if (action == "save") {
            String stored;
            serializeJson(request, stored);
            if (!preferences.putString("websettings", stored)) {
                panelReply(500, "{\"error\":\"保存失败\"}");
                return;
            }
            panelSaved.set(request);
        }
        panelPreview.set(request);
    } else {
        panelReply(400, "{\"error\":\"操作无效\"}");
        return;
    }
    panelReply(200, "{\"ok\":true}");
}

void receiveTelemetry(const char* body) {
    StaticJsonDocument<2048> incoming;
    if (deserializeJson(incoming, body) ||
        !incoming["interfaces"].is<JsonArray>() || !incoming["mounts"].is<JsonArray>()) {
        panelReply(400, "{\"error\":\"指标格式有误\"}");
        return;
    }
    nasMetrics.set(incoming);
    hasNasData = true;
    lastNasData = millis();
    panelReply(200, "{\"ok\":true}");
}

void setupPanel() {
    deserializeJson(panelSaved, panelDefaults);
    StaticJsonDocument<2048> stored;
    if (!deserializeJson(stored, preferences.getString("websettings")) &&
        panelSettingsValid(stored.as<JsonObjectConst>())) panelSaved.set(stored);
    else {
        panelSaved["speed"] = baseline.speed;
        panelSaved["brightness"] = baseline.brightness;
        panelSaved["groups"] = baseline.groups;
        panelSaved["orientation"] = baseline.orientation;
        panelSaved["reverse"] = bool(baseline.reverseModules);
    }
    panelPreview.set(panelSaved);
}

void receivePanelRequest(const char* input) {
    if (strcmp(input, "HELLO") == 0) {
        Serial.println("READY LIFE1 USB");
        return;
    }
    char action[16];
    int offset = 0;
    if (sscanf(input, "Q %lu %15s%n", &requestId, action, &offset) != 2 ||
        (input[offset] != ' ' && input[offset] != '\0')) return;
    panelRequested = true;
    const char* body = input + offset + (input[offset] == ' ');
    if (strcmp(action, "state") == 0) serveState();
    else if (strcmp(action, "index") == 0) panelReply(200, index_html);
    else if (strcmp(action, "css") == 0) panelReply(200, panel_css);
    else if (strcmp(action, "js") == 0) panelReply(200, panel_js);
    else if (strcmp(action, "telemetry") == 0) receiveTelemetry(body);
    else writePanel(action, body);
}

int mapped(const char* metric, const char* low, const char* high,
           const char* outputLow, const char* outputHigh, int fallback) {
    const auto value = nasMetrics[metric];
    if (!value.is<float>()) return fallback;
    const float ratio = constrain((value.as<float>() - panelPreview[low].as<int>()) /
                                  (panelPreview[high].as<int>() - panelPreview[low].as<int>()), 0.0f, 1.0f);
    const int minimum = panelPreview[outputLow].as<int>();
    return lroundf(minimum + ratio * (panelPreview[outputHigh].as<int>() - minimum));
}

void servicePanel() {
    const bool linked = panelPreview["linked"].as<bool>();
    const bool online = nasOnline();
    liveSpeed = panelPreview["speed"];
    liveBrightness = panelPreview["brightness"];
    liveGroups = panelPreview["groups"];
    liveOrientation = panelPreview["orientation"];
    liveReverse = panelPreview["reverse"].as<bool>();
    liveDark = !panelPreview["power"].as<bool>() || (linked && panelPreview["offline"].as<bool>() && !online);
    liveLifetime = 0;
    if (linked && online) {
        if (panelPreview["network"].as<bool>()) liveSpeed = mapped("network","networkMin","networkMax","speedMin","speedMax",liveSpeed);
        if (panelPreview["cpu"].as<bool>()) liveBrightness = mapped("cpu","cpuMin","cpuMax","brightMin","brightMax",liveBrightness);
        if (panelPreview["disk"].as<bool>()) liveLifetime = mapped("disk","diskMin","diskMax","lifeMin","lifeMax",0);
    }
    if (webReseed) {
        seed(liveGroups);
        webReseed = false;
    }
}
