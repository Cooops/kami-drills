# v2 gap coding (pre-registration draft)

A replay gap is a tool call the drill has no recorded response for. The replay answers it with
"not available in this drill (no recorded response)" and records the tool name in the score's `misses` (v1 uses the
same signal). The rule below is fixed before Season 2 results exist and is implemented in `kami_drills/scoring_v2.py`
(`gap_code`).

1. Take each attempt's calls after the fork, in order. A call is a *gap* if the replay had no response for it;
   a call that got a response is a *scorable action* only if it is state-changing (`STATE_CHANGING` in
   `kami_drills/cadence.py`). Reads are never scorable actions: a model that read and then hit a gap had not yet acted.
2. **not_administered**: the first gap comes before any scorable action. The model never got to act on a working
   world, so the attempt says nothing about the decision tested.
3. **scored**: a gap comes after at least one scorable action. The decision was already made; the drill rule's
   verdict (pass or fail) stands as computed.
4. **clean**: no gap.
5. Report per model, beside the pass rate: the share of attempts in each code, and two scores: not_administered
   excluded from the denominator, and not_administered counted as 0 (`gap_sensitivity`). The two are co-primary and
   are always reported together: exclusion is unbiased only if non-administration is independent of ability, and the
   score-0 value bounds the damage when it is not. The forecast's decision score (proposal section 6.1) uses the
   excluded score; a ranking claim holds only if it holds under both.
6. Neither the coding nor the co-primary pair may change after results are seen. A drill whose gap rate is high is
   fixed in the suite for the next season, not recoded.

Limit: the proposal words rule 3 as a gap after a *wrong* action. Where the code cannot tell right from wrong, it
scores any gap that follows an action; a gap after a right action does not change a pass.
