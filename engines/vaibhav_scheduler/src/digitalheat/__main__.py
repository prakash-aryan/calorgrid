"""python -m digitalheat: print the reference assumptions as a Markdown table."""

from .assumptions import reference_assumptions

print(reference_assumptions().to_markdown())
