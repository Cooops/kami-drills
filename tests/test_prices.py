"""Token pricing checks: uv run python tests/test_prices.py"""
from inspect_ai.model import ModelUsage

from kami_drills.report import price_call

# a logged haiku call: 62161 prompt tokens (36339 cache write), 247 completion; OpenRouter billed $0.007248075
u = ModelUsage(input_tokens=25822, input_tokens_cache_write=36339, input_tokens_cache_read=0, output_tokens=247,
               reasoning_tokens=171, total_tokens=62408)
assert abs(price_call("anthropic/claude-haiku-5.5", u) - 0.007248075) < 1e-9

table = {"anthropic/x": {"pricing": {"prompt": "0.000001", "completion": "0.000002", "input_cache_read": "0.0000001",
                                     "overrides": [{"min_prompt_tokens": 1000, "prompt": "0.000002", "completion": "0.000004"}]}},
         "other/y": {"pricing": {"prompt": "0.000001", "completion": "0.000002"}}}
small = ModelUsage(input_tokens=100, input_tokens_cache_read=200, input_tokens_cache_write=400, output_tokens=10)
# anthropic without a write price: 1.25x prompt; elsewhere writes and reads bill as prompt
assert abs(price_call("anthropic/x", small, table) - (100e-6 + 200e-7 + 400 * 1.25e-6 + 20e-6)) < 1e-12
assert abs(price_call("other/y", small, table) - (700e-6 + 20e-6)) < 1e-12
# the long-prompt tier applies on total prompt tokens (uncached + read + write)
big = ModelUsage(input_tokens=1000, output_tokens=10)
assert abs(price_call("anthropic/x", big, table) - (2000e-6 + 40e-6)) < 1e-12
assert price_call("unknown/z", big, table) is None and price_call("anthropic/x", None, table) is None
print("ok")
