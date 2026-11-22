"""
tests/test_command_loop_check_script.py

Runs the manual Kafka check script against the in-memory bus, so its own
logic (both cases, the exit code) is verified even though the real broker
is not available to the tests.
"""

from scripts.command_loop_kafka_check import main


def test_the_check_script_passes_on_the_in_memory_bus(capsys):
    assert main(["--in-memory", "--timeout", "10"]) == 0
    out = capsys.readouterr().out
    assert "Case 1: in contact: CONFIRMED" in out
    assert "Case 2: out of contact: FAILED" in out
    assert "RESULT: PASS" in out
