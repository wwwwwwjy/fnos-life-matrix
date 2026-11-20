from pathlib import Path
import json

Import("env")

project = Path(env.subst("$PROJECT_DIR"))
build = Path(env.subst("$BUILD_DIR"))
build.mkdir(parents=True, exist_ok=True)
assets = []
for name in ("index.html", "panel.css", "panel.js"):
    body = (project / "panel" / name).read_text()
    identifier = name.replace(".", "_")
    assets.append(f"static const char {identifier}[] PROGMEM = R\"MATRIX({body})MATRIX\";")
(build / "panel_assets.h").write_text("\n".join(assets))
env.Append(CPPPATH=[str(build)])
