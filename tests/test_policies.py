"""Scripted policies, no model calls: the oracle must pass every drill, the null policy must fail every drill.
Run: uv run python tests/test_policies.py"""
import tempfile

from inspect_ai import eval as inspect_eval

from kami_drills.task import kamidrills


def passes(policy):
    with tempfile.TemporaryDirectory() as logs:
        log = inspect_eval(kamidrills(policy=policy), model="mockllm/model", log_dir=logs, display="none")[0]
        return {s.id: s.scores["drill_rule"].value == "C" for s in log.samples}, [s.id for s in log.samples if s.error]


oracle, oracle_err = passes("oracle")
null, null_err = passes("null")
n = len(oracle)
print(f"oracle  {sum(oracle.values())}/{n} pass   null  {sum(null.values())}/{n} pass")
assert not oracle_err and not null_err, f"sample errors: {oracle_err + null_err}"
assert all(oracle.values()), f"oracle fails: {sorted(k for k, v in oracle.items() if not v)}"
assert not any(null.values()), f"null passes: {sorted(k for k, v in null.items() if v)}"
