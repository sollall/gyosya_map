from fixtures import make_xlsx

from gyosya_map.cli import main


def test_cli_end_to_end(tmp_path, capsys):
    dbp, raw = str(tmp_path / "g.db"), str(tmp_path / "raw")
    assert main(["--db", dbp, "init"]) == 0
    f = make_xlsx(tmp_path / "k.xlsx", [["第1号", "株式会社サンプル解体", "見本", "川崎市", "令和8年4月1日", None]])
    assert main(["--db", dbp, "--raw-dir", raw, "import", "--scheme", "pref-kanagawa.demolition", "--file", str(f)]) == 0
    capsys.readouterr()
    assert main(["--db", dbp, "check", "--address", "川崎市幸区1", "--trade", "解体", "--name", "サンプル解体"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("【公認あり】神奈川県の神奈川県解体工事業登録あり（有効期限：令和13年3月31日）")
    assert main(["--db", dbp, "check", "--address", "東京都港区1", "--trade", "water", "--name", "x"]) == 2
