import json

from ct_toolkit.utils.trajectory_scrub import Scrubber

KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz1234"


class TestScrubText:
    def test_masks_each_category(self):
        hits = []
        text = Scrubber(env_secrets=False).scrub_text(
            "mail jane.doe@example.com from /Users/jane/x via 8.8.8.8 and 10.0.0.5 key " + KEY, "f.txt", hits
        )
        assert "jane.doe@example.com" not in text and "<email>" in text
        assert "/Users/<user>/x" in text
        assert "8.8.8.8" not in text and "<ip>" in text
        assert "10.0.0.5" in text  # private addresses are not sensitive
        assert KEY not in text
        assert {h.category for h in hits} == {"email", "home directory", "public IP", "credential"}

    def test_hits_never_contain_the_original_value(self):
        hits = []
        Scrubber(env_secrets=False).scrub_text(f"key {KEY}", "f.txt", hits)
        assert all(KEY not in h.preview for h in hits)

    def test_credential_found_once_is_masked_bare_elsewhere(self):
        scrubber = Scrubber(env_secrets=False)
        scrubber._learn(f"first line has {KEY}")
        assert KEY not in scrubber.scrub_text(f"printed bare: {KEY}")

    def test_listed_values_are_masked(self):
        text = Scrubber(redact=["ProjectNebula"], env_secrets=False).scrub_text("ProjectNebula ships Friday")
        assert "ProjectNebula" not in text

    def test_env_secrets_are_masked_but_not_ordinary_variables(self):
        env = {"MY_API_TOKEN": "tok-1234567890", "DATA_ROOT": "/srv/app/data", "SHORT_SECRET": "abc"}
        text = Scrubber(env=env).scrub_text("tok-1234567890 /srv/app/data abc")
        assert "tok-1234567890" not in text
        assert "/srv/app/data" in text  # not secret-looking
        assert "abc" in text  # too short to be a secret


class TestScrubJson:
    def test_json_stays_valid_and_keys_are_scrubbed(self):
        data = {"steps": [{"message": "write to jane@example.com", "tool": {f"key_{KEY}": KEY}}], "n": 3}
        cleaned = Scrubber(env_secrets=False).scrub_object(data)
        assert json.loads(json.dumps(cleaned))  # round-trips
        assert cleaned["steps"][0]["message"] == "write to <email>"
        assert KEY not in json.dumps(cleaned)
        assert cleaned["n"] == 3


class TestScrubPaths:
    def _trajectory(self, tmp_path):
        run = tmp_path / "run"
        run.mkdir()
        (run / "trace.json").write_text(json.dumps({"msg": f"use {KEY}", "ip": "8.8.8.8"}), encoding="utf-8")
        (run / "session.jsonl").write_text(
            json.dumps({"out": "clean line"}) + "\n" + json.dumps({"out": f"echo {KEY}"}) + "\n", encoding="utf-8"
        )
        (run / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
        (run / "binary.bin").write_bytes(b"\xff\xfe\x00bad")
        return run

    def test_writes_scrubbed_copies_and_copies_clean_files_byte_for_byte(self, tmp_path):
        run = self._trajectory(tmp_path)
        out = tmp_path / "out"
        report = Scrubber(env_secrets=False).scrub_paths([run], out=out, root=run)

        assert KEY not in (out / "trace.json").read_text()
        assert json.loads((out / "trace.json").read_text())["ip"] == "<ip>"
        lines = (out / "session.jsonl").read_text().splitlines()
        assert lines[0] == json.dumps({"out": "clean line"})  # unchanged line kept as it was
        assert KEY not in lines[1]
        assert (out / "clean.txt").read_bytes() == (run / "clean.txt").read_bytes()
        assert not (out / "binary.bin").exists()
        assert str((run / "binary.bin")) in report.skipped
        assert report.files_changed == 2

    def test_dry_run_writes_nothing(self, tmp_path):
        run = self._trajectory(tmp_path)
        report = Scrubber(env_secrets=False).scrub_paths([run])
        assert report.hits and not (tmp_path / "out").exists()


class TestWindowsHomeDirectories:
    def test_windows_user_name_is_masked_in_text_and_json(self, tmp_path):
        scrubber = Scrubber(env_secrets=False)
        assert scrubber.scrub_text(r"cwd C:\Users\jane\Work\proj") == r"cwd C:\Users\<user>\Work\proj"
        cleaned = scrubber.scrub_object({"cwd": r"C:\Users\jane\Work"})
        assert cleaned == {"cwd": r"C:\Users\<user>\Work"}
