import ast
from pathlib import Path


def test_signal_scanner_link_has_no_embedded_credentials():
    source = Path(__file__).resolve().parents[1] / "app_bootstrap.py"
    tree = ast.parse(source.read_text())
    links = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "tradingbot-production-ed44.up.railway.app" in node.value
    ]
    assert links == ["https://tradingbot-production-ed44.up.railway.app/"]
