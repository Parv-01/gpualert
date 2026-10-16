"""Fault-injection and robustness harness.

These tests do not check features. They check that the wrapper keeps its
contract while the world misbehaves: signals arriving at awkward moments,
filesystems that refuse writes, children that spawn children, output that
is not valid text, and many wrappers running at once on one node.

Tests that currently fail are marked xfail(strict=True) with the defect
id they document. Strict means a fix turns the xfail into a hard failure,
which is the signal to promote it to an ordinary test. Nothing here is
allowed to pass quietly for the wrong reason.
"""
