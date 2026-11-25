"""mihomo 入口脚本与两份配置模板。本机没有 sh / awk 时跳过脚本部分。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "docker" / "mihomo-entrypoint.sh"
EXAMPLE = ROOT / "clash" / "config.example.yaml"
DIRECT = ROOT / "clash" / "config.direct.yaml"

HAS_SH = shutil.which("sh") is not None and shutil.which("awk") is not None
needs_sh = pytest.mark.skipif(not HAS_SH, reason="本机没有 sh / awk")


@pytest.mark.parametrize("template", [EXAMPLE, DIRECT], ids=lambda path: path.name)
def test_template_is_valid_yaml(template: Path) -> None:
    data = yaml.safe_load(template.read_text("utf-8"))
    assert (data["mixed-port"], data["allow-lan"], data["mode"]) == (7890, True, "rule")
    assert data["dns"]["enable"] is True
    # forager 是另一个容器：代理端口必须允许「局域网」访问
    assert data["bind-address"] == "*"
    # hanime1 的解析定向到境外 DoH，避开本地 DNS 与部分国内 DoH 的污染
    assert "+.hanime1.me" in data["dns"]["nameserver-policy"]


def test_example_template_defaults_hanime1_to_direct() -> None:
    data = yaml.safe_load(EXAMPLE.read_text("utf-8"))
    assert data["proxy-providers"]["airport"]["url"] == "__SUBSCRIBE_URL__"
    assert data["proxy-groups"][0]["use"] == ["airport"]
    assert "DOMAIN-SUFFIX,hanime1.me,DIRECT" in data["rules"]  # 经节点访问会被 Cloudflare 挑战
    assert data["rules"][-1] == "MATCH,PROXY"


def test_direct_template_has_no_placeholder_and_goes_direct() -> None:
    text = DIRECT.read_text("utf-8")
    assert "__SUBSCRIBE_URL__" not in text
    assert yaml.safe_load(text)["rules"] == ["MATCH,DIRECT"]


def render(home: Path, url: str) -> subprocess.CompletedProcess[str]:
    home.mkdir(parents=True)  # 模拟挂载进容器的配置目录
    for template in (EXAMPLE, DIRECT):
        shutil.copy(template, home / template.name)
    env = {
        **os.environ,
        "CLASH_HOME": str(home).replace(os.sep, "/"),
        "CLASH_SUBSCRIBE_URL": url,
        "MIHOMO_BIN": "true",  # 不真的启动 mihomo
    }
    return subprocess.run(
        ["sh", str(SCRIPT).replace(os.sep, "/")],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


@needs_sh
def test_subscription_url_is_rendered_verbatim(tmp_path) -> None:
    url = "https://airport.example/sub?token=a&b=1|2$3"
    home = tmp_path / "mihomo"

    result = render(home, url)

    assert result.returncode == 0, result.stderr
    rendered = home / "config.yaml"
    assert rendered.read_text("utf-8") == EXAMPLE.read_text("utf-8").replace("__SUBSCRIBE_URL__", url)
    provider = yaml.safe_load(rendered.read_text("utf-8"))["proxy-providers"]["airport"]
    assert provider["url"] == url
    assert not (home / "config.yaml.tmp").exists()
    if os.name != "nt":  # 产物里有订阅地址
        assert rendered.stat().st_mode & 0o777 == 0o600


@needs_sh
def test_empty_url_falls_back_to_the_direct_config(tmp_path) -> None:
    home = tmp_path / "mihomo"

    result = render(home, "")

    assert result.returncode == 0, result.stderr
    assert "CLASH_SUBSCRIBE_URL" in result.stderr
    assert (home / "config.yaml").read_text("utf-8") == DIRECT.read_text("utf-8")


@needs_sh
@pytest.mark.parametrize(
    "url",
    [
        "airport.example/sub",  # 没有 scheme
        "ftp://airport.example/sub",
        'https://airport.example/"x',
        "https://airport.example/\\x",
        "https://airport.example/a b",
        "https://airport.example/a\tb",
    ],
)
def test_unusable_urls_are_rejected(tmp_path, url: str) -> None:
    home = tmp_path / "mihomo"

    result = render(home, url)

    assert result.returncode != 0
    assert "CLASH_SUBSCRIBE_URL" in result.stderr
    assert not (home / "config.yaml").exists()
    assert not (home / "config.yaml.tmp").exists()
