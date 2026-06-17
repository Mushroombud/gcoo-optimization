from __future__ import annotations

from pathlib import Path


DEFAULT_WIDGET_SCRIPT = "./hermes_widget.js"


def inject_hermes_widget(path: Path, script_src: str = DEFAULT_WIDGET_SCRIPT) -> None:
    html = path.read_text(encoding="utf-8")
    if "hermes_widget.js" in html:
        return
    block = f'<script defer src="{script_src}"></script>'
    if "</body>" in html:
        html = html.replace("</body>", f"  {block}\n</body>", 1)
    elif "</html>" in html:
        html = html.replace("</html>", f"{block}\n</html>", 1)
    else:
        html = f"{html}\n{block}\n"
    path.write_text(html, encoding="utf-8")
