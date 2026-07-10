# Synthetic examples

Generate the local example tree with invented, deterministic bytes only:

```bash
uv run robolake example generate examples/synthetic-dataset --seed 7
```

The generated directory is ignored by Git. Remove it before changing the seed or rerunning the
command because the generator intentionally refuses to overwrite non-empty destinations.
