#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <SPI.h>
#include <algorithm>
#include <array>
#include <esp_system.h>
#include "panel_assets.h"

namespace {
constexpr int dataPin = 23;
constexpr int clockPin = 18;
constexpr int selectPin = 27;
constexpr int width = 32;
constexpr int height = 8;
constexpr unsigned telemetryTimeout = 15000;
constexpr unsigned diagnosticInterval = 50;
using Grid = std::array<uint32_t, height>;

struct Baseline {
    uint8_t speed;
    uint8_t brightness;
    uint8_t groups;
    uint8_t offlineDark;
    uint8_t orientation;
    uint8_t reverseModules;
};

Preferences preferences;
Baseline baseline{3, 5, 5, 0, 5, 0};
Grid grid{};
constexpr Grid calibrationGrid{
    0x0101010f, 0x01010101, 0x01010101, 0x00000001,
    0x00000001, 0x00000000, 0x0f070301, 0x00000000
};
bool calibrating = false;
std::array<Grid, 8> history{};
size_t historySize = 0;
unsigned generation = 0;
uint32_t lastTick = 0;
uint32_t lastFrame = 0;
int liveSpeed, liveBrightness, liveGroups, liveLifetime, liveDark, liveOrientation, liveReverse;
int shownBrightness = -1;
bool shownDark = true;
char line[4096];
size_t lineLength = 0;
bool discardLine = false;
String webRows;
unsigned displayedGeneration = 0;
int displayedSpeed = 3;
bool displayedCalibration = false;
void seed(int groups);

#include "web_panel.h"

bool valid(int speed, int brightness, int groups, int orientation, int reverseModules) {
    return speed >= 1 && speed <= 10 && brightness >= 0 && brightness <= 15 &&
           groups >= 1 && groups <= 20 && orientation >= 0 && orientation <= 7 &&
           reverseModules >= 0 && reverseModules <= 1;
}

void seed(int groups) {
    grid.fill(0);
    generation = 0;
    historySize = 0;
    for (int group = 0; group < groups; ++group) {
        const int x = esp_random() % width;
        const int y = esp_random() % height;
        grid[y] |= uint32_t(1) << x;
        if (x > 0) grid[y] |= uint32_t(1) << (x - 1);
        if (x < width - 1) grid[y] |= uint32_t(1) << (x + 1);
        if (y > 0) grid[y - 1] |= uint32_t(1) << x;
        if (y < height - 1) grid[y + 1] |= uint32_t(1) << x;
    }
}

void advance(int groups, int lifetime) {
    Grid next{};
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            int neighbors = 0;
            for (int row = max(0, y - 1); row <= min(height - 1, y + 1); ++row) {
                for (int col = max(0, x - 1); col <= min(width - 1, x + 1); ++col) {
                    if (row != y || col != x) neighbors += (grid[row] >> col) & 1;
                }
            }
            if (neighbors == 3 || (neighbors == 2 && ((grid[y] >> x) & 1))) {
                next[y] |= uint32_t(1) << x;
            }
        }
    }
    if (next == Grid{} || next == grid ||
        std::find(history.begin(), history.begin() + historySize, next) != history.begin() + historySize ||
        (lifetime > 0 && generation >= unsigned(lifetime))) {
        seed(groups);
        return;
    }
    historySize = std::min(historySize + 1, history.size());
    std::move_backward(history.begin(), history.begin() + historySize - 1, history.begin() + historySize);
    history.front() = next;
    grid = next;
    ++generation;
}

void writeRegister(uint8_t address, const std::array<uint8_t, 4>& values) {
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
    digitalWrite(selectPin, LOW);
    for (auto value : values) {
        SPI.transfer(address);
        SPI.transfer(value);
    }
    digitalWrite(selectPin, HIGH);
    SPI.endTransaction();
}

void setRegister(uint8_t address, uint8_t value) {
    writeRegister(address, {value, value, value, value});
}

void draw(const Grid& display, int orientation, bool reverseModules) {
    std::array<std::array<uint8_t, 4>, 8> pixels{};
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            if (((display[y] >> x) & 1) == 0) continue;
            int bit = x % 8;
            int row = y;
            const int module = reverseModules ? 3 - x / 8 : x / 8;
            if (orientation & 1) std::swap(row, bit);
            if (orientation & 2) row = 7 - row;
            if (orientation & 4) bit = 7 - bit;
            pixels[row][module] |= uint8_t(1 << bit);
        }
    }
    for (int row = 0; row < 8; ++row) writeRegister(row + 1, pixels[row]);
}

void readSerial() {
    for (int remaining = min(Serial.available(), 256); remaining > 0; --remaining) {
        const int character = Serial.read();
        if (character == '\n') {
            if (!discardLine) {
                line[lineLength] = '\0';
                receivePanelRequest(line);
            }
            lineLength = 0;
            discardLine = false;
        } else if (character != '\r' && !discardLine) {
            if (lineLength < sizeof(line) - 1) line[lineLength++] = char(character);
            else discardLine = true;
        }
    }
}
}  // namespace

void setup() {
    Serial.setRxBufferSize(4096);
    Serial.begin(115200);
    preferences.begin("life1", false);
    Baseline stored;
    if (preferences.getBytesLength("baseline") == sizeof(stored) &&
        preferences.getBytes("baseline", &stored, sizeof(stored)) == sizeof(stored) &&
        valid(stored.speed, stored.brightness, stored.groups, stored.orientation, stored.reverseModules) &&
        stored.offlineDark <= 1) baseline = stored;
    pinMode(selectPin, OUTPUT);
    digitalWrite(selectPin, HIGH);
    SPI.begin(clockPin, -1, dataPin, selectPin);
    setRegister(0x0c, 0);
    setRegister(0x0f, 0);
    setRegister(0x09, 0);
    setRegister(0x0b, 7);
    seed(baseline.groups);
    Serial.println("READY LIFE1");
    setupPanel();
}

void loop() {
    readSerial();
    servicePanel();
    const uint32_t now = millis();
    if (!calibrating && now - lastTick >= unsigned(50 * (11 - liveSpeed))) {
        advance(liveGroups, liveLifetime);
        lastTick = now;
    }
    if (now - lastFrame >= diagnosticInterval) {
        lastFrame = now;
        const Grid& display = calibrating ? calibrationGrid : grid;
        const bool dark = liveDark;
        if (liveBrightness != shownBrightness) {
            setRegister(0x0a, liveBrightness);
            shownBrightness = liveBrightness;
        }
        if (!dark) draw(display, liveOrientation, liveReverse);
        if (dark != shownDark) {
            setRegister(0x0c, dark ? 0 : 1);
            shownDark = dark;
        }
        displayedGeneration = generation;
        displayedSpeed = liveSpeed;
        displayedCalibration = calibrating;
        char encoded[65];
        for (size_t index = 0; index < display.size(); ++index) {
            snprintf(encoded + index * 8, 9, "%08lx", static_cast<unsigned long>(dark ? 0 : display[index]));
        }
        webRows = encoded;
        if (!panelRequested) {
            Serial.printf("F LIFE1 %u %d %d %d %s\n", generation, liveSpeed, shownBrightness, dark, encoded);
        }
    }
    delay(1);
}
