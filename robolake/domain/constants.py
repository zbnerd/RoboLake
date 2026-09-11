"""Deployment-independent RoboLake protocol constants."""

MAX_DATASET_NAME_BYTES = 255
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_ENTRIES = 100_000
MAX_PATH_SEGMENT_BYTES = 255
MAX_RELATIVE_PATH_BYTES = 1_024

# M2 multipart protocol constants. These values are stored-session semantics,
# never deployment configuration.
BASE_PART_BYTES = 64 * 1024 * 1024
MAX_PART_BYTES = 5 * 1024 * 1024 * 1024
MAX_PARTS = 10_000
MAX_MULTIPART_BLOB_BYTES = 5_000_000_000_000
PART_PLAN_SCHEMA_VERSION = 1
