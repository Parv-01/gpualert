"""Signals arriving at the wrapper, at the points where they actually arrive.

On a cluster the wrapper is far more likely to be signalled than to exit
on its own: scancel sends SIGTERM, preemption sends SIGTERM, the node
OOM-killer sends SIGKILL, a dropped SSH session sends SIGHUP. Every one of
those is a job ending, and every one of them deserves a result.
"""

from __future__ import annotations

import signal
import time

import pytest

from tests.robustness import faults, probes

pytestmark = [pytest.mark.robustness, pytest.mark.slow]


def _running_wrapper(tree, tmp_path, **kwargs):
    """Start a wrapper around a long silent job and wait until it is streaming."""
    wrapper = probes.Wrapper(faults.silent(seconds=120), tree=tree, **kwargs)
    probes.wait_for(lambda: bool(probes.descendants(wrapper.pid)), timeout=15)
    time.sleep(0.5)
    return wrapper


class TestSigterm:
    @pytest.mark.xfail(
        strict=True,
        reason="D2: SIGTERM is not handled, so the wrapper dies at rc -15 with no "
        "result — scancel and Slurm preemption are never reported",
    )
    def test_sigterm_produces_a_job_result(self, tree, tmp_path):
        wrapper = _running_wrapper(tree, tmp_path)
        try:
            wrapper.signal(signal.SIGTERM)
            result = wrapper.collect(timeout=25)
            assert result is not None, "wrapper died without producing a result"
            assert result["status"] in ("interrupted", "cancelled", "preempted", "failed")
        finally:
            wrapper.kill_tree()

    @pytest.mark.xfail(
        strict=True,
        reason="D2: no handler means no finalisation, so the log has no footer",
    )
    def test_sigterm_still_finalises_the_log(self, tree, tmp_path):
        wrapper = _running_wrapper(tree, tmp_path)
        try:
            wrapper.signal(signal.SIGTERM)
            result = wrapper.collect(timeout=25)
            assert result is not None
            assert probes.has_footer(result["combined_log_path"])
        finally:
            wrapper.kill_tree()

    @pytest.mark.xfail(
        strict=True,
        reason="D2/D3: an unhandled SIGTERM leaves the wrapped job running with "
        "its GPU memory still allocated",
    )
    def test_sigterm_does_not_orphan_the_job(self, tree, tmp_path):
        wrapper = _running_wrapper(tree, tmp_path)
        try:
            children = [c.pid for c in probes.descendants(wrapper.pid)]
            assert children, "no child to observe"
            wrapper.signal(signal.SIGTERM)
            wrapper.collect(timeout=25)
            assert probes.survivors(children) == []
        finally:
            wrapper.kill_tree()


class TestSigint:
    def test_sigint_is_handled_and_reported(self, tree, tmp_path):
        # KeyboardInterrupt is the one signal run_job already understands.
        wrapper = _running_wrapper(tree, tmp_path)
        try:
            wrapper.signal(signal.SIGINT)
            result = wrapper.collect(timeout=25)
            assert result is not None
            assert result["status"] == "interrupted"
        finally:
            wrapper.kill_tree()

    @pytest.mark.xfail(
        strict=True,
        reason="D3: the interrupt path kills only the direct child",
    )
    def test_sigint_does_not_orphan_the_job(self, tree, tmp_path):
        wrapper = probes.Wrapper(faults.spawns_grandchild(str(tmp_path / "worker.pid")), tree=tree)
        try:
            worker = probes.read_pid_file(tmp_path / "worker.pid", timeout=15)
            assert worker is not None
            wrapper.signal(signal.SIGINT)
            wrapper.collect(timeout=25)
            assert probes.survivors([worker]) == []
        finally:
            wrapper.kill_tree()


class TestSighup:
    @pytest.mark.xfail(
        strict=True,
        reason="D2: SIGHUP (dropped SSH session) is unhandled and kills the wrapper",
    )
    def test_sighup_produces_a_job_result(self, tree, tmp_path):
        wrapper = _running_wrapper(tree, tmp_path)
        try:
            wrapper.signal(signal.SIGHUP)
            result = wrapper.collect(timeout=25)
            assert result is not None
        finally:
            wrapper.kill_tree()


class TestSigkill:
    def test_logs_survive_an_unblockable_kill(self, tree, tmp_path):
        """SIGKILL cannot be handled, so the contract is what is already on disk.

        This is the pre-launch log guarantee earning its keep: the files
        exist and hold everything streamed up to the moment of death, even
        though no finalisation code ever ran.
        """
        home = tmp_path / "wrapper_home"
        home.mkdir()
        wrapper = probes.Wrapper(faults.chatty(lines=60, interval=0.15), tree=tree, home=str(home))
        try:
            assert probes.wait_for(
                lambda: any(
                    "step" in probes.log_text(str(p)) for p in probes.combined_logs_under(str(home))
                ),
                timeout=20,
            ), "nothing was streamed to disk before the kill"

            wrapper.proc.kill()
            wrapper.collect(timeout=15)

            logs = probes.combined_logs_under(str(home))
            assert logs, "no log directory survived the kill"
            body = probes.log_text(str(logs[0]))
            assert "=== GPUAlert Job Log ===" in body, "header missing"
            assert "step" in body, "streamed output did not reach disk"
            assert not probes.has_footer(str(logs[0])), (
                "a SIGKILLed wrapper cannot have written a footer; if it did, "
                "the test is not measuring what it claims"
            )
        finally:
            wrapper.kill_tree()
