"""Theme configuration for Libre_Core AutoLoader TUI."""

AUTOLOADER_CSS = """
Screen {
    background: #1a1a2e;
    color: #e0e0e0;
}
Button {
    background: #333333;
    color: #ffffff;
    border: none;
    margin: 1 2;
}
Button:focus {
    background: #555555;
}
Button.-primary {
    background: #ee1c25;
}
Button.-primary:focus {
    background: #ff3333;
}
.warning {
    color: #ffcc00;
}
.error {
    color: #ff3333;
}
.success {
    color: #33cc33;
}
Header {
    background: #0f0f1c;
    color: #ee1c25;
}
Footer {
    background: #0f0f1c;
    color: #ffffff;
}
.header-text {
    text-align: center;
    text-style: bold;
    color: #ee1c25;
    margin: 2 0;
}
.container {
    padding: 1 2;
}
.button-container {
    layout: horizontal;
    align: center middle;
    height: auto;
}
RadioSet {
    background: #252540;
    border: solid #ee1c25;
    padding: 1 2;
    margin: 1 0;
}
.section-title {
    text-style: bold;
    color: #ffffff;
    background: #2b2b40;
    padding: 0 1;
    margin: 1 0;
}
.warning-panel {
    background: #2e1a1a;
    border: solid #ee1c25;
    padding: 1 2;
    margin: 1 0;
}
.button-row {
    layout: horizontal;
    align: center middle;
    height: auto;
    margin: 1 0;
}
.pathways-scroll {
    height: 1fr;
    scrollbar-gutter: stable;
}
"""
