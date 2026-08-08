# Compare semantic versions numerically

`is_compatible(current, minimum)` compares version strings lexicographically.
That gives wrong answers when a numeric component has more than one digit.

Repair the implementation without changing the public API. Versions contain
dot-separated non-negative integer components. Missing trailing components are
equivalent to zero. Malformed versions must raise `ValueError`.
