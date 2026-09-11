"""Stable, safe RoboLake domain errors."""

from __future__ import annotations


class RoboLakeError(Exception):
    """Base class for expected RoboLake failures."""

    code = "ROBOLAKE_ERROR"


class InvalidDatasetName(RoboLakeError):
    """Raised when a Dataset name is ambiguous or unsafe."""

    code = "INVALID_DATASET_NAME"


class InvalidDatasetReference(RoboLakeError):
    """Raised when a Dataset reference lacks a positive version suffix."""

    code = "INVALID_DATASET_REFERENCE"


class InvalidDigestError(RoboLakeError):
    """Raised when a SHA-256 digest is not canonical lowercase hexadecimal."""

    code = "INVALID_DIGEST"


class UnsafePathError(RoboLakeError):
    """Raised when a logical path cannot be reconstructed safely."""

    code = "UNSAFE_PATH"


class PathCollisionError(RoboLakeError):
    """Raised when manifest paths cannot coexist portably."""

    code = "PATH_COLLISION"


class UnsafeFileTypeError(RoboLakeError):
    """Raised when a source tree contains a symlink or special file."""

    code = "UNSAFE_FILE_TYPE"


class UnsupportedFileSizeError(RoboLakeError):
    """Raised when a file requires multipart support unavailable in M1."""

    code = "UNSUPPORTED_FILE_SIZE"

    def __init__(self, relative_path: str, size_bytes: int) -> None:
        super().__init__(
            f"{relative_path} is {size_bytes} bytes; multipart upload is an M2 feature."
        )


class SourceChangedError(RoboLakeError):
    """Raised when a source file changes during a stable read."""

    code = "SOURCE_CHANGED"

    def __init__(self, relative_path: str) -> None:
        super().__init__(f"{relative_path} changed while being read; rescan and rerun.")


class UnsupportedPlatformError(RoboLakeError):
    """Raised when required safe filesystem primitives are unavailable."""

    code = "UNSUPPORTED_PLATFORM"


class ManifestMismatchError(RoboLakeError):
    """Raised when manifest content is invalid or non-canonical."""

    code = "MANIFEST_MISMATCH"


class ContentConflictError(RoboLakeError):
    """Raised when one content identity is associated with conflicting attributes."""

    code = "CONTENT_CONFLICT"


class IllegalTransitionError(RoboLakeError):
    """Raised when a lifecycle transition is not part of the state machine."""

    code = "ILLEGAL_TRANSITION"


class IdempotencyConflictError(RoboLakeError):
    """Raised when one caller key is reused for a different request payload."""

    code = "IDEMPOTENCY_CONFLICT"


class NotFoundError(RoboLakeError):
    """Raised when a requested logical RoboLake resource does not exist."""

    code = "NOT_FOUND"


class InvalidCursorError(RoboLakeError):
    """Raised when a download cursor is malformed or bound elsewhere."""

    code = "INVALID_CURSOR"


class StoredObjectMismatchError(RoboLakeError):
    """Raised when stored bytes do not match their immutable content address."""

    code = "STORED_OBJECT_MISMATCH"


class UploadConflictError(RoboLakeError):
    """Raised when a conditional upload outcome is not yet visible."""

    code = "UPLOAD_CONFLICT"


class ApiProtocolError(RoboLakeError):
    """Raised when the control plane violates its stable response contract."""

    code = "API_PROTOCOL_ERROR"


class ApiUnavailableError(RoboLakeError):
    """Raised when the control plane cannot be reached safely."""

    code = "API_UNAVAILABLE"


class RetryableTransferError(RoboLakeError):
    """Raised for retryable direct object-transfer failures."""

    code = "RETRYABLE_TRANSFER"


class TransferRejectedError(RoboLakeError):
    """Raised when storage rejects a direct transfer permanently."""

    code = "TRANSFER_REJECTED"


class OutputExistsError(RoboLakeError):
    """Raised when atomic publication would replace an existing output."""

    code = "OUTPUT_EXISTS"


class UnsupportedFilesystemError(RoboLakeError):
    """Raised when atomic no-replace directory publication is unavailable."""

    code = "UNSUPPORTED_FILESYSTEM"


class LocalFileChangedError(RoboLakeError):
    """Raised when a resumed source no longer proves the sealed Blob."""

    code = "LOCAL_FILE_CHANGED"


class InvalidPartNumberError(RoboLakeError):
    """Raised when a part number is outside the frozen plan."""

    code = "INVALID_PART_NUMBER"


class PartSizeMismatchError(RoboLakeError):
    """Raised when provider part size differs from the frozen plan."""

    code = "PART_SIZE_MISMATCH"


class PartChecksumRejectedError(RoboLakeError):
    """Raised when a part checksum is invalid or rejected."""

    code = "PART_CHECKSUM_REJECTED"


class MultipartSessionNotFoundError(RoboLakeError):
    """Raised when the provider attempt is absent and no final object resolves it."""

    code = "MULTIPART_SESSION_NOT_FOUND"


class AdmissionLeaseHeldError(RoboLakeError):
    """Raised when another unexpired invocation owns upload admission."""

    code = "ADMISSION_LEASE_HELD"


class AdmissionCapacityExhaustedError(RoboLakeError):
    """Raised when all multipart upload admission slots are active."""

    code = "ADMISSION_CAPACITY_EXHAUSTED"


class AdmissionLeaseLostError(RoboLakeError):
    """Raised when an upload mutation no longer owns the current fence."""

    code = "ADMISSION_LEASE_LOST"


class MultipartInitiationInProgressError(RoboLakeError):
    """Raised when provider initiation has no durable addressable outcome yet."""

    code = "MULTIPART_INITIATION_IN_PROGRESS"


class MultipartInitiationAmbiguousError(RoboLakeError):
    """Raised when provider initiation may have succeeded without a durable ID."""

    code = "MULTIPART_INITIATION_AMBIGUOUS"


class MultipartCompletionAmbiguousError(RoboLakeError):
    """Raised when multipart completion has no proven final outcome yet."""

    code = "MULTIPART_COMPLETION_AMBIGUOUS"


class FinalBlobPublicationConflictError(RoboLakeError):
    """Raised when conditional final publication requires reconciliation."""

    code = "FINAL_BLOB_PUBLICATION_CONFLICT"


class ProviderContractError(RoboLakeError):
    """Raised when object storage violates the required provider profile."""

    code = "PROVIDER_CONTRACT_ERROR"


class ProviderOperationRejectedError(RoboLakeError):
    """Raised when object storage definitively rejects a control operation."""

    code = "PROVIDER_OPERATION_REJECTED"


class TransientProviderFailureError(RoboLakeError):
    """Raised when a provider operation can be retried without changing identity."""

    code = "TRANSIENT_PROVIDER_FAILURE"


class MultipartAbortAmbiguousError(RoboLakeError):
    """Raised when an abort may have settled but no response proves the outcome."""

    code = "MULTIPART_ABORT_AMBIGUOUS"
