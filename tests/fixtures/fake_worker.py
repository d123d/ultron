"""Fake agent for tests. Usage: fake_worker.py <mode> <prompt_file> <cwd>"""
import json
import pathlib
import sys
import time

mode, prompt_file, cwd = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
prompt = pathlib.Path(prompt_file).read_text(encoding="utf-8")


def result(status: str, summary: str, files: list[str]) -> None:
    print("FLEET_RESULT: " + json.dumps({"status": status, "summary": summary, "files": files}))


if mode == "ok":
    (cwd / "hello.txt").write_text("hello", encoding="utf-8")
    print("working on it")
    result("done", "wrote hello", ["hello.txt"])
elif mode == "liar":
    result("done", "wrote hello (not really)", ["hello.txt"])
elif mode == "quota":
    print("Error: You exceeded your usage limit (429)")
    sys.exit(1)
elif mode == "error":
    print("boom", file=sys.stderr)
    sys.exit(3)
elif mode == "sleep":
    time.sleep(60)
elif mode == "echo":
    print(prompt)
    result("done", "echoed", [])
elif mode == "half":  # wrong first try; fixes it when handed the failing checks
    (cwd / "hello.txt").write_text("hello" if "REPAIR:" in prompt else "hel", encoding="utf-8")
    result("done", "wrote hello", ["hello.txt"])
elif mode == "cheat":  # passes by gutting the existing test its check runs
    (cwd / "tests" / "test_hello.py").write_text("def test_hello():\n    pass\n", encoding="utf-8")
    result("done", "fixed", ["tests/test_hello.py"])
elif mode == "silent":
    pass
